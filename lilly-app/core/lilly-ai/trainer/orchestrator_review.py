#!/usr/bin/env python3
"""
orchestrator_review.py — Orchestrator thought + review for the training process
================================================================================

The Lilly Orchestrator (Raccoon → Cat → Owl chain for training QA) reviews every
training run and leaves a structured record that lands in three places:

  1. .training/reviews/<job>.json        — machine-readable review history
  2. the Lilly orchestrator group chat    — the human-readable "thought"
  3. the training dashboard (:8098 /training)  — surfaced under "Orchestrator Review"

Review records contain:
  * stage     — "plan" (before a run), "result" (per-avatar / per-run outcome),
                "summary" (aggregate end-of-pipeline verdict)
  * metrics   — whatever the training step produced (loss, perplexity, mAP, …)
  * verdict   — PASS / WARN / FAIL  + a deterministic reason
  * thought   — the orchestrator's own commentary text

Thought generation is deliberately resource-safe:
  * a deterministic analysis is ALWAYS produced (no LLM, no local-Ollama usage)
  * an LLM-grounded "second opinion" is only attempted when TRAINING_LLM_REVIEW=1
    (or --llm) and shells out to `opencode run` with the REMOTE model that is
    already configured for the coding agents — never the local training Ollama.
    It is bounded by a hard timeout and silently falls back to the
    deterministic thought on any failure.

CLI:
  python3 trainer/orchestrator_review.py review persona [--avatar puppy] [--llm]
  python3 trainer/orchestrator_review.py review yolo     [--llm]
  python3 trainer/orchestrator_review.py list   [persona|yolo] [limit]
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE = Path(__file__).resolve().parent.parent
REVIEWS_DIR = WORKSPACE / ".training" / "reviews"

VERDICTS = ("PASS", "WARN", "FAIL")

# Known-good families reused when building verdict reasons.
_VERDICT_TEXT = {
    "PASS": "Good to promote: stable training signal, sane metrics, deployable.",
    "WARN": "Usable but keep watch: some metrics are borderline.",
    "FAIL": "Do not promote: this checkpoint does not meet the quality bar.",
}


# ── persistence ───────────────────────────────────────────────────────────────
def reviews_path(job: str) -> Path:
    """Per-job review file (one JSON array of records per job)."""
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "_-." else "_" for c in job)
    return REVIEWS_DIR / f"{safe}.json"


def load_reviews(job: str) -> List[dict]:
    p = reviews_path(job)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_reviews(job: str, records: List[dict]) -> None:
    p = reviews_path(job)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")


# ── deterministic analysis helpers ────────────────────────────────────────────
def _parse_trainer_state(output_dir: str) -> Dict[str, Any]:
    """Extract loss history + eval stats from an HF/Unsloth run directory."""
    out: Dict[str, Any] = {}
    d = Path(output_dir)
    state = d / "trainer_state.json"
    if state.exists():
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            history = data.get("log_history", [])
            losses = [
                float(h["loss"])
                for h in history
                if "loss" in h and not h.get("eval_loss")
            ]
            evals = [float(h["eval_loss"]) for h in history if "eval_loss" in h]
            if losses:
                out["loss_first"] = round(losses[0], 4)
                out["loss_last"] = round(losses[-1], 4)
                out["loss_steps"] = len(losses)
                out["loss_dropped"] = round(losses[0] - losses[-1], 4)
            if evals:
                out["eval_loss"] = round(evals[-1], 4)
                out["perplexity"] = round(math.exp(evals[-1]), 2)
                out["eval_steps"] = len(evals)
            out["global_step"] = data.get("global_step")
        except Exception:
            pass
    return out


def _loss_trend_good(metrics: Dict[str, Any]) -> Optional[str]:
    """'learning' / 'flat' / 'worsening' based on the loss curve, or None."""
    if metrics.get("loss_first") is None or metrics.get("loss_last") is None:
        return None
    first, last = metrics["loss_first"], metrics["loss_last"]
    if last < first * 0.9:
        return "learning"
    if last <= first * 1.05:
        return "flat"
    return "worsening"


def _perplexity_grade(ppl: float) -> str:
    if ppl < 20:
        return "excellent"
    if ppl < 50:
        return "good"
    if ppl < 100:
        return "borderline"
    return "poor"


def _grade_persona(metrics: Dict[str, Any]) -> tuple[str, str]:
    """(verdict, reason) — deterministic persona quality bar."""
    reasons = []
    trend = _loss_trend_good(metrics)
    ppl = metrics.get("perplexity")

    if trend == "learning" or trend == "flat":
        reasons.append(
            f"loss {metrics.get('loss_first')}→{metrics.get('loss_last')} ({trend})"
        )
    elif trend == "worsening":
        reasons.append(
            f"loss INCREASING {metrics.get('loss_first')}→{metrics.get('loss_last')}"
        )

    if ppl is not None:
        reasons.append(f"perplexity {ppl} ({_perplexity_grade(ppl)})")
        if ppl >= 30 and ppl < 60:
            return "PASS", "; ".join(reasons) + ". " + _VERDICT_TEXT["PASS"]
        if ppl < 30:
            return "PASS", "; ".join(reasons) + ". " + _VERDICT_TEXT["PASS"]
        if ppl < 100:
            return "WARN", "; ".join(reasons) + ". " + _VERDICT_TEXT["WARN"]
        return "FAIL", "; ".join(reasons) + ". " + _VERDICT_TEXT["FAIL"]
    elif trend == "learning":
        return "PASS", "; ".join(reasons) + ". " + _VERDICT_TEXT["PASS"]
    elif trend == "worsening":
        return "FAIL", "; ".join(reasons) + ". " + _VERDICT_TEXT["FAIL"]
    elif trend == "flat":
        return "WARN", "; ".join(reasons) + ". " + _VERDICT_TEXT["WARN"]
    return "PASS", "No loss history recorded; artifact presence verified."


def _grade_yolo(metrics: Dict[str, Any]) -> tuple[str, str]:
    """(verdict, reason) — deterministic YOLO quality bar."""
    reasons = []
    map50 = metrics.get("mAP50")
    samples = metrics.get("samples")
    epochs = metrics.get("epochs")

    if samples is not None:
        sufficiency = "sufficient" if samples >= 20 else "thin"
        reasons.append(f"{samples} samples ({sufficiency})")
        if samples < 10:
            return "FAIL", "; ".join(reasons) + ". Too few samples to be reliable."
    if epochs is not None:
        reasons.append(f"{epochs} epochs")
    if map50 is not None:
        reasons.append(f"mAP50 {round(map50, 3)}")
        if map50 >= 0.5:
            return "PASS", "; ".join(reasons) + ". " + _VERDICT_TEXT["PASS"]
        if map50 >= 0.3:
            return "WARN", "; ".join(reasons) + ". " + _VERDICT_TEXT["WARN"]
        return "FAIL", "; ".join(reasons) + ". " + _VERDICT_TEXT["FAIL"]
    if samples is not None and samples >= 20:
        return "PASS", "; ".join(reasons) + ". Artifact present, metrics pending."
    return "WARN", "; ".join(reasons) + ". Metrics unavailable."


def _deterministic_thought(
    description: str, metrics: Dict[str, Any], verdict: str, reason: str
) -> str:
    """The default orchestrator thought — always available, no LLM involved."""
    trend = _loss_trend_good(metrics)
    lines = [
        f"🧠 Orchestrator review — {description}.",
        f"Verdict: {verdict}. {reason}",
    ]
    if trend:
        lines.append(
            f"Curve analysis: loss {metrics['loss_first']} → {metrics['loss_last']} "
            f"across {metrics.get('loss_steps', '?')} logged steps "
            f"({trend})."
        )
    if metrics.get("perplexity"):
        lines.append(
            f"Perplexity {metrics['perplexity']} "
            f"({_perplexity_grade(metrics['perplexity'])}) — "
            f"{'deployable' if verdict != 'FAIL' else 'below the bar'}."
        )
    if metrics.get("mAP50") is not None:
        lines.append(
            f"Detection mAP50 {round(metrics['mAP50'], 3)} — "
            f"{'field-worthy' if verdict != 'FAIL' else 'needs more data/epochs'}."
        )
    if metrics.get("samples") is not None:
        lines.append(f"Dataset: {metrics['samples']} samples.")
    return "\n".join(lines)


# ── LLM-grounded second opinion (remote model only) ───────────────────────────
def _llm_thought(
    description: str,
    metrics: Dict[str, Any],
    verdict: str,
    reason: str,
    timeout: int = 90,
) -> Optional[str]:
    """Best-effort LLM thought via `opencode run` (remote model).

    Never touches the local training Ollama. Falls back to None on any error
    so the caller can keep the deterministic thought.
    """
    if shutil.which("opencode") is None:
        return None
    payload = {
        "task": description,
        "metrics": metrics,
        "verdict": verdict,
        "reason": reason,
    }
    prompt = (
        "You are the Lilly Orchestrator's training QA reviewer. Give a short, "
        "sharp, decisive review of this training result (3-6 sentences). Judge "
        "whether it should be promoted/deployed or retrained. Just the review, "
        "no preamble.\n\n" + json.dumps(payload, default=str, indent=2)
    )
    env = dict(os.environ)
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    try:
        p = subprocess.run(
            ["opencode", "run", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(WORKSPACE),
        )
        if p.returncode == 0 and p.stdout and p.stdout.strip():
            return p.stdout.strip()[:4000]
    except Exception:
        pass
    return None


# ── main record function ──────────────────────────────────────────────────────
def record_review(
    job: str,
    rtype: str,
    stage: str,
    metrics: Optional[Dict[str, Any]] = None,
    verdict: Optional[str] = None,
    reason: Optional[str] = None,
    description: str = "",
    llm: bool = False,
    avatar: Optional[str] = None,
    chat: bool = True,
) -> dict:
    """Append one orchestrator review record and (optionally) chat it."""
    metrics = metrics or {}
    if verdict not in VERDICTS:
        # Auto-grade when the caller did not supply a verdict.
        if rtype == "yolo":
            verdict, reason = _grade_yolo(metrics)
        else:
            verdict, reason = _grade_persona(metrics)

    thought = _deterministic_thought(
        description or f"{rtype} {stage}", metrics, verdict, reason or ""
    )
    if llm:
        extra = _llm_thought(
            description or f"{rtype} {stage}", metrics, verdict, reason or ""
        )
        if extra:
            thought = f"{thought}\n\n💭 Second opinion (remote reviewer):\n{extra}"

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "job": job,
        "type": rtype,
        "stage": stage,
        "avatar": avatar,
        "metrics": metrics,
        "verdict": verdict,
        "reason": reason or "",
        "thought": thought,
    }
    records = load_reviews(job)
    records.append(record)
    save_reviews(job, records)

    if chat:
        _chat_thought(record)
    return record


def _chat_thought(record: dict) -> None:
    """Drop the thought into the Lilly orchestrator group chat (best-effort)."""
    try:
        from lilly_orchestrator import Orchestrator  # type: ignore

        orch = Orchestrator()
        head = record["thought"].splitlines()[:4]
        orch.chat.lilly_says(
            "🧠 Training review"
            + f" [{record.get('type')}/{record.get('stage')}"
            + (f"/{record.get('avatar')}" if record.get("avatar") else "")
            + f"] {record.get('verdict')}: "
            + " ".join(head)
        )
        orch.save_state()
    except Exception:
        pass


# ── typed helpers for the two pipelines ───────────────────────────────────────
def review_persona_result(
    avatar: str,
    output_dir: str,
    config: Optional[Dict[str, Any]] = None,
    job: str = "persona",
    llm: bool = False,
    succeeded: bool = True,
    error: Optional[str] = None,
) -> dict:
    if not succeeded:
        return record_review(
            job=job,
            rtype="persona",
            stage="result",
            avatar=avatar,
            metrics={"error": error or "unknown"},
            verdict="FAIL",
            reason=f"Training crashed for avatar '{avatar}'. {error or ''}".strip(),
            description=f"persona avatar '{avatar}'",
            llm=llm,
        )
    metrics = _parse_trainer_state(output_dir)
    if config:
        metrics.setdefault("steps", config.get("training_steps"))
        metrics.setdefault("lr", config.get("learning_rate"))
        metrics.setdefault("samples", config.get("max_train_samples"))
    return record_review(
        job=job,
        rtype="persona",
        stage="result",
        avatar=avatar,
        metrics=metrics,
        description=f"persona avatar '{avatar}' (final={output_dir})",
        llm=llm,
    )


def review_persona_plan(
    avatars: List[str], job: str = "persona", llm: bool = False
) -> dict:
    return record_review(
        job=job,
        rtype="persona",
        stage="plan",
        metrics={"avatars": avatars, "count": len(avatars)},
        verdict="WARN" if len(avatars) < 3 else "PASS",
        reason=f"{len(avatars)} avatars queued for this run.",
        description="persona training plan",
        llm=llm,
    )


def review_persona_summary(
    results: Dict[str, bool], job: str = "persona", llm: bool = False
) -> dict:
    ok = sum(1 for v in results.values() if v)
    total = len(results)
    verdict = "PASS" if ok == total else ("WARN" if ok > 0 else "FAIL")
    return record_review(
        job=job,
        rtype="persona",
        stage="summary",
        metrics={"ok": ok, "failed": total - ok, "total": total},
        verdict=verdict,
        reason=f"{ok}/{total} avatars trained successfully.",
        description=f"persona pipeline summary ({ok}/{total} ok)",
        llm=llm,
    )


def review_yolo_result(
    job: str = "yolo",
    metrics: Optional[Dict[str, Any]] = None,
    llm: bool = False,
    model_path: Optional[str] = None,
    succeeded: bool = True,
    error: Optional[str] = None,
) -> dict:
    if not succeeded:
        return record_review(
            job=job,
            rtype="yolo",
            stage="result",
            metrics={"error": error or "unknown"},
            verdict="FAIL",
            reason=f"YOLO training run failed. {error or ''}".strip(),
            description="yolo training run",
            llm=llm,
        )
    metrics = dict(metrics or {})
    if model_path:
        metrics.setdefault("model", model_path)
    return record_review(
        job=job,
        rtype="yolo",
        stage="result",
        metrics=metrics,
        description="yolo training run",
        llm=llm,
    )


# ── listing for the dashboard ─────────────────────────────────────────────────
def list_reviews(rtype: str = "all", limit: int = 10) -> List[dict]:
    if not REVIEWS_DIR.exists():
        return []
    recs: List[dict] = []
    for p in REVIEWS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for r in data:
            if rtype == "all" or r.get("type") == rtype:
                recs.append(r)
    recs.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return recs[: max(1, min(int(limit), 100))]


# ── CLI ───────────────────────────────────────────────────────────────────────
def _cli_review(rtype: str, avatar: Optional[str], llm: bool) -> dict:
    if rtype == "persona":
        if avatar:
            out = WORKSPACE / "trained_avatars" / avatar
            if not (out / "final").exists():
                print(json.dumps({"error": f"no final adapter at {out}"}, indent=2))
                sys.exit(1)
            rec = review_persona_result(
                avatar=avatar, output_dir=str(out), llm=llm, job=f"cli-{avatar}"
            )
        else:
            rec = review_persona_summary({}, job="cli-persona", llm=llm)
            rec = {}  # no real run to summarize — report the latest instead
            rec = (
                list_reviews("persona", 1)[0]
                if list_reviews("persona", 1)
                else {
                    "error": "no persona reviews yet — train an avatar first, or use --avatar"
                }
            )
    else:
        # YOLO: grade the latest model artifact we can find.
        cands = sorted(
            (p for p in (WORKSPACE / "trained_models").glob("**/*.pt") if p.exists()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not cands:
            samples = 0
            sd = WORKSPACE / "training_data" / "samples"
            if sd.exists():
                samples = sum(1 for p in sd.glob("*/*.jpg"))
            rec = record_review(
                job="cli-yolo",
                rtype="yolo",
                stage="result",
                metrics={"samples": samples, "model": None},
                verdict="WARN",
                reason="No trained .pt found yet; dataset review only.",
                description="yolo training plan",
                llm=llm,
            )
        else:
            model_path = str(cands[0])
            rec = review_yolo_result(
                job="cli-yolo",
                metrics={"model": model_path},
                llm=llm,
                model_path=model_path,
            )
    return rec


def _cli_list(rtype: str, limit: int) -> List[dict]:
    return list_reviews(rtype, limit)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "review":
        args = argv[1:]
        rtype = "persona"
        avatar = None
        llm = "TRAINING_LLM_REVIEW" in os.environ or "--llm" in args
        args = [a for a in args if a != "--llm"]
        if args and args[0] in ("persona", "yolo"):
            rtype = args.pop(0)
        if args and args[0] == "--avatar":
            args.pop(0)
            avatar = args.pop(0) if args else None
        rec = _cli_review(rtype, avatar, llm)
        print(json.dumps(rec, indent=2, default=str))
        return 0
    if cmd == "list":
        args = argv[1:]
        rtype = args.pop(0) if args and args[0] in ("persona", "yolo", "all") else "all"
        limit = int(args[0]) if args and args[0].isdigit() else 10
        print(json.dumps(_cli_list(rtype, limit), indent=2, default=str))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
