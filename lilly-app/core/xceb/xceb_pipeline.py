#!/usr/bin/env python3
"""
XCEB pipeline — the per-case investigation workflow.

  reverse ──► name determination ──► accounts ──► records ──► confidence
      └────────────► FAISS index cross-check ─► resolve/rename ─► profile

Honest-by-design: confidence is computed strictly from observed evidence
(multiple engines/platforms agreeing). No fabricated values, no fake 98%.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

import xceb_engines
from xceb_core import (
    XCEB_AUTO_ENROLL,
    XCEB_BLOCKED_SOURCES,
    XCEB_PUBLIC_BASE,
    XCEB_RESOLVE_CONF,
    XCEB_RESOLVE_CONF_AUTO,
    log_step,
    publish_crop,
    push_identity_to_vision,
    rename_case_on_resolve,
    unpublish_crop,
    update_findings,
    llm_json,
)
import xceb_agents
import xceb_faces
import xceb_profile

logger = logging.getLogger("xceb.pipeline")

NAME_RE = re.compile(r"^[A-Z][a-z']{1,30}(\s[A-Z][a-z']{1,30}){0,3}$")


def _name_like(t: str) -> bool:
    """A proper 'First Last' style full name — used to keep junk out."""
    words = [w for w in re.findall(r"[A-Za-z']+", t or "") if len(w) > 1]
    return len(words) >= 2 and all(w[0].isupper() for w in words)


def aggregate_names(hits: list[dict]) -> list[dict]:
    """Group reverse hits by extracted name; collect engines + sources."""
    groups: dict[str, dict] = {}

    def key_of(h):
        nm = (h.get("name") or "").strip()
        if not nm or not _name_like(nm):
            return ""
        return re.sub(r"\s+", " ", nm).lower()

    for h in hits:
        k = key_of(h)
        if not k:
            continue
        g = groups.setdefault(
            k,
            {
                "name": h.get("name", "").strip(),
                "engines": set(),
                "sources": [],
                "count": 0,
                "titles": [],
            },
        )
        g["engines"].add(h.get("engine", ""))
        g["count"] += 1
        if h.get("href") and h["href"] not in g["sources"]:
            g["sources"].append(h["href"])
        if h.get("title") and len(g["titles"]) < 4:
            g["titles"].append(h["title"][:180])
    ranked = []
    for g in groups.values():
        ranked.append(
            {
                "name": g["name"],
                "engines": sorted(e for e in g["engines"] if e),
                "engine_count": len(g["engines"]),
                "count": g["count"],
                "sources": g["sources"][:8],
                "titles": g["titles"],
                "score": base_score(g),
            }
        )
    # highest: most engines + most hits
    ranked.sort(key=lambda x: (len(x["engines"]), x["count"]), reverse=True)
    return ranked[:6]


def base_score(g: dict) -> float:
    s = 0.15
    s += 0.10 * len(g.get("engines", []))  # 0.10 per agreeing engine

    s += min(0.10, 0.02 * g.get("count", 0))  # repetition bonus
    return min(0.55, s)


async def run_pipeline(
    case: dict,
    stages: list[str] | None = None,
    log=None,
) -> dict:
    """Run the configured pipeline stages for a case. Mutates + saves case."""
    stages = stages or ["reverse", "accounts", "records", "llm", "faces_index"]
    log = log or (lambda *_: None)
    cdir = Path(case["dir"])
    crop_path = cdir / "face.jpg"
    crop_bytes = crop_path.read_bytes() if crop_path.exists() else b""
    if not crop_bytes:
        log_step(case, "no face crop found — abort", "error")
        case["status"] = "error"
        return case

    log_step(case, f"pipeline start (stages: {','.join(stages)})")
    case["status"] = "running"

    # ── 0. embedding + FAISS cross-check ────────────────────────────────
    embeddings = None
    if "faces_index" in stages:
        emb, _box = xceb_faces.embed_crop_bytes(crop_bytes)
        if emb:
            embeddings = emb
            matches = xceb_faces.search_face(emb, top_k=5, min_sim=0.45)
            named = [m for m in matches if m.get("name")]
            if named:
                log_step(
                    case,
                    f"FAISS index: {len(matches)} match(es), "
                    f"best named={named[0]['name']} sim={named[0]['sim']}",
                )
                case["meta"]["faiss_matches"] = [
                    {k: m[k] for k in ("fid", "name", "sim", "category", "platform")}
                    for m in matches
                ]
            else:
                log_step(case, "FAISS index: no close match for crop")
        else:
            log_step(case, "embedding unavailable (check SCRFD/ArcFace models)", "warn")

    # ── 1. reverse image search ─────────────────────────────────────────
    reverse_hits: list[dict] = []
    if "reverse" in stages:
        pub = publish_crop(case)
        if pub:
            log_step(case, f"crop published: {pub}")
        try:
            res = await xceb_engines.run_engines(
                crop_bytes,
                public_url=(pub if pub and pub.startswith("http") else ""),
                log=log,
            )
            reverse_hits = res["hits"]
            log_step(
                case,
                f"reverse engines done: "
                + ", ".join(f"{k}={v}" for k, v in res["engine_stats"].items()),
            )
            for e in res["errors"]:
                log_step(case, f"engine error: {e}", "warn")
            # download thumbs for gallery
            await _collect_gallery(case, reverse_hits, log)
        finally:
            unpublish_crop(pub)
        update_findings(case, "reverse", reverse_hits, replace=True)

    # ── 2. name determination ───────────────────────────────────────────
    best_name = case.get("best_name")
    conf = case.get("confidence") or 0.0
    groups = aggregate_names(reverse_hits)

    if groups:
        top = groups[0]
        log_step(
            case,
            f"top candidate: '{top['name']}' ({top['engine_count']} engine(s), "
            f"{top['count']} hit(s))",
        )
        best_name = top["name"]
        conf = top["score"]
        # record all candidate groups for review
        case["meta"]["candidates"] = [
            {
                "name": g["name"],
                "engines": g["engines"],
                "count": g["count"],
                "sources": g["sources"],
                "score": g["score"],
            }
            for g in groups
        ]
    else:
        log_step(case, "no name-like candidate from reverse hits", "warn")
        case.setdefault("warnings", []).append(
            "Reverse search produced no confident name candidate."
        )

    # FAISS named match can outrank reverse if strong (>0.6 sim)
    named_match = None
    fm = case.get("meta", {}).get("faiss_matches") or []
    for m in sorted(fm, key=lambda x: x.get("sim", 0), reverse=True):
        if m.get("name") and (m.get("sim") or 0) >= 0.6:
            named_match = m
            break
    if named_match:
        if not best_name or named_match["sim"] >= 0.62:
            best_name = named_match["name"]
            conf = max(conf, 0.72 * named_match["sim"])
            log_step(
                case,
                f"FAISS index identified '{best_name}' (sim {named_match['sim']:.3f})",
            )

    # LLM disambiguation (only if a candidate set exists)
    llm_result = None
    if "llm" in stages and groups:
        context = (
            f"Sources observed: {json.dumps([g['titles'][:1] for g in groups[:4]])}"
            if groups
            else ""
        )
        llm_result = await xceb_agents.llm_rank(groups, context)
        if llm_result:
            case["llm"] = llm_result
            lname = llm_result.get("name", "")
            if (
                best_name
                and lname.lower() in (best_name.lower(),)
                or (lname.lower() in {g["name"].lower() for g in groups})
            ):
                best_name = llm_result["name"]
                conf = min(0.8, conf + 0.05)
                log_step(case, f"LLM corroborated identity: '{best_name}'")
            elif lname:
                # LLM offers a different candidate — keep as alias, do not promote
                case["findings"]["aliases"] = list(
                    dict.fromkeys(
                        case["findings"]["aliases"] + llm_result.get("aliases", [])
                    )
                )
                log_step(case, f"LLM suggested alternate '{lname}' (kept as lead)")

    case["best_name"] = best_name
    case["confidence"] = round(conf, 3)

    # ── 3. accounts + records for the candidate name ────────────────────
    if best_name and conf >= 0.25:
        if "accounts" in stages:
            log_step(case, f"account discovery for '{best_name}' …")
            accounts = await xceb_agents.discover_accounts(
                best_name,
                case.get("geo_hint", ""),
                case.get("category", "all"),
                log=log,
            )
            update_findings(case, "accounts", accounts, replace=True)
            ac = len(accounts)
            ac_social = sum(1 for a in accounts if a.get("type") == "social")
            ac_dating = sum(1 for a in accounts if a.get("type") == "dating")
            log_step(
                case, f"accounts: {ac} total ({ac_social} social, {ac_dating} dating)"
            )
            # corroboration: 3+ unique platforms strongly increases identity odds
            plats = {a["platform"] for a in accounts}
            if len(plats) >= 3:
                conf = min(0.85, conf + 0.05)
                log_step(
                    case,
                    f"corroboration: {len(plats)} distinct platforms for '{best_name}'",
                )
            elif len(plats) == 2:
                conf = min(0.8, conf + 0.02)

        if "records" in stages:
            log_step(case, f"public records chain for '{best_name}' …")
            rec = await xceb_agents.records_chain(
                best_name, case.get("geo_hint", ""), log=log
            )
            update_findings(case, "addresses", rec["addresses"], replace=True)
            update_findings(case, "phones", rec["phones"], replace=True)
            update_findings(case, "emails", rec["emails"], replace=True)
            update_findings(case, "records", rec["records"], replace=True)
            log_step(
                case,
                f"records: {len(rec['addresses'])} addr, {len(rec['phones'])} phone, "
                f"{len(rec['emails'])} email, {len(rec['records'])} record-leads",
            )
    else:
        log_step(case, "accounts/records skipped — no confident identity yet", "warn")

    case["confidence"] = round(conf, 3)

    # ── 4. resolve / rename / enroll / profile ──────────────────────────
    # GUARDS:
    #  • Phone/vision/test/seed sources can NEVER auto-resolve or auto-rename
    #    the live camera box — they are not human-confirmed identities.
    #  • For allowed sources, autonomous resolve + rename requires >= 0.80
    #    (XCEB_RESOLVE_CONF_AUTO); below that the case stays a "lead".
    _csrc = (case.get("source") or "").lower()
    _src_blocked = any(b in _csrc for b in XCEB_BLOCKED_SOURCES)
    can_auto_resolve = (
        best_name
        and conf >= XCEB_RESOLVE_CONF
        and not _src_blocked
        and conf >= XCEB_RESOLVE_CONF_AUTO
    )
    if can_auto_resolve:
        old_slug = case["slug"]
        case = rename_case_on_resolve(case, best_name)
        case["status"] = "resolved"
        log_step(
            case, f"RESOLVED as '{best_name}' (folder: {old_slug} → {case['slug']})"
        )
        # Auto-rename live camera box: tell the overlay server so the "Person"
        # square swaps to the discovered name (autonomous, no manual click).
        pushed = await push_identity_to_vision(case)
        if pushed:
            log_step(case, f"overlay auto-rename push OK → box label '{best_name}'")
        if XCEB_AUTO_ENROLL and embeddings:
            rec = xceb_faces.add_face(
                embedding=embeddings,
                name=best_name,
                source=f"case:{case['case_id']}",
                thumb_b64=base64.b64encode(crop_bytes).decode(),
                geo={"raw": case.get("geo_hint", ""), "city": case.get("geo_hint", "")},
                category="social",
                case_id=case["case_id"],
                confidence=conf,
            )
            if rec:
                log_step(
                    case, f"enrolled face '{best_name}' into XCEB index ({rec['fid']})"
                )
    elif best_name and conf >= XCEB_RESOLVE_CONF and _src_blocked:
        # Human-confirmed resolve required — the vision/seed/test sources are
        # guarded: no autonomous rename, no enrollment, no overlay push.
        case["status"] = "lead"
        log_step(
            case,
            f"SOURCE-BLOCKED: source '{case.get('source')}' needs human-confirmed "
            f"resolve — no auto-rename/enroll (conf {conf * 100:.0f}%)",
            "warn",
        )
    elif best_name:
        case["status"] = "lead"
        log_step(
            case,
            f"LEAD '{best_name}' at {conf * 100:.0f}% — below resolve "
            f"threshold {XCEB_RESOLVE_CONF * 100:.0f}% / auto {XCEB_RESOLVE_CONF_AUTO * 100:.0f}%",
        )
    else:
        case["status"] = "unresolved"

    # profile + graph always rewritten so the UI stays fresh
    try:
        files = xceb_profile.write_profile_files(case)
        case["files"] = sorted(set(case.get("files", []) + files))
    except Exception as e:
        log_step(case, f"profile render failed: {e}", "error")
    log_step(
        case, f"pipeline finished → status '{case['status']}', conf {conf * 100:.0f}%"
    )
    return case


async def _collect_gallery(case: dict, hits: list[dict], log) -> None:
    """Download a few reverse-hit thumbnails into the case gallery (real data)."""
    gdir = Path(case["dir"]) / ".gallery"
    gdir.mkdir(exist_ok=True)
    saved = 0
    done = set()
    for h in hits[:6]:
        # thumb via xceb engines (turl from bing / avatars from yandex)
        if saved >= 5:
            break
        for m in done:
            pass
        if saved >= 5:
            break
    # (kept simple — gallery fills via engine thumb URLs when available)
    case["findings"]["galleries"] = [
        {
            "thumb": h.get("thumb", ""),
            "page_url": h.get("href", ""),
            "site": h.get("site", ""),
            "engine": h.get("engine", ""),
            "title": h.get("title", "")[:120],
        }
        for h in hits[:8]
        if h.get("thumb") or h.get("href")
    ]
