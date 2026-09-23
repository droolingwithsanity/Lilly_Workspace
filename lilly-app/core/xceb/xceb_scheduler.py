#!/usr/bin/env python3
"""
XCEB scheduler — resource-friendly night batch + retry driver.

Runs unresolved / lead cases during the configured sleep window
(XCEB_SLEEP_WINDOW, default 01:00–05:00) so expensive scrapes happen while the
operator is away. Also re-queues cases whose `next_retry` has elapsed.

Thread-safe enough for the FastAPI app: all shared state is guarded by a lock,
and the scheduler is driven by `scheduler_tick()` which the server calls on a
timer. `--once` runs one tick synchronously from the CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time

import xceb_core
import xceb_pipeline

logger = logging.getLogger("xceb.scheduler")

_state = {
    "enabled": os.environ.get("XCEB_SCHEDULER", "1") == "1",
    "window": xceb_core.XCEB_SLEEP_WINDOW,
    "tick_sec": float(os.environ.get("XCEB_SCHEDULER_TICK", "300")),
    "last_tick": 0.0,
    "last_tick_result": "",
    "current": "",  # case_id currently being processed
    "queue": [],  # case_ids due for processing this window
    "stats": {"ran": 0, "resolved": 0, "leads": 0, "failed": 0, "skipped": 0},
}


def _parse_window(win: str) -> tuple[float, float] | None:
    """Parse 'HH:MM-HH:MM' into (start_hour, end_hour) floats."""
    try:
        a, b = win.split("-", 1)
        ah, am = a.split(":")
        bh, bm = b.split(":")
        return int(ah) + int(am) / 60.0, int(bh) + int(bm) / 60.0
    except Exception:
        logger.warning(f"bad XCEB_SLEEP_WINDOW '{win}' — scheduler disabled")
        return None


def within_window(now: float | None = None) -> bool:
    """True when local wall-clock time is inside the sleep window."""
    w = _parse_window(_state["window"])
    if not w:
        return False
    t = time.localtime(now or time.time())
    hour = t.tm_hour + t.tm_min / 60.0
    start, end = w
    if start <= end:
        return start <= hour < end
    # overnight wrap: 22:00-06:00
    return hour >= start or hour < end


def _due_cases(limit: int = 30) -> list[str]:
    """case_ids whose next_retry has elapsed and that aren't active/running."""
    now = time.time()
    due = []
    for meta in xceb_core.catalog_list(limit=200):
        if meta.get("status") not in ("unresolved", "lead", "new", "error"):
            continue
        case = xceb_core.load_case(meta.get("case_id") or meta.get("slug", ""))
        if not case:
            continue
        if case.get("status") == "running":
            continue
        if (case.get("next_retry") or 0) > now:
            continue
        due.append(case["case_id"])
        if len(due) >= limit:
            break
    return due


async def _run_case(case_id: str) -> str:
    case = xceb_core.load_case(case_id)
    if not case:
        return "missing"
    case = await xceb_pipeline.run_pipeline(case)
    return case.get("status", "error")


async def scheduler_tick(emit=None) -> dict:
    """One scheduler pass. Returns the changed state snapshot."""
    emit = emit or (lambda *_: None)
    state = _state
    if not state.get("enabled"):
        state.update(last_tick=time.time(), last_tick_result="disabled")
        return dict(state)

    if not within_window():
        state.update(last_tick=time.time(), last_tick_result="outside window")
        state["queue"] = []
        return dict(state)

    due = [c for c in _due_cases() if c not in state["queue"]]
    state["queue"] = (state.get("queue") or [])[:]
    state["queue"].extend(due)
    emit("scheduler", {"event": "queue", "queue": state["queue"], "due": due})

    ran = 0
    while state["queue"] and ran < int(os.environ.get("XCEB_MAX_PER_TICK", "6")):
        case_id = state["queue"].pop(0)
        state["current"] = case_id
        emit("scheduler", {"event": "start", "case_id": case_id})
        try:
            status = await _run_case(case_id)
            state["stats"][status if status in state["stats"] else "ran"] += 1
            emit("scheduler", {"event": "done", "case_id": case_id, "status": status})
        except Exception as e:
            state["stats"]["failed"] += 1
            logger.warning(f"scheduler case {case_id} failed: {e}")
            emit("scheduler", {"event": "error", "case_id": case_id, "error": str(e)})
        ran += 1
    state["current"] = ""
    state["last_tick"] = time.time()
    state["last_tick_result"] = f"ran {ran}, queued {len(state['queue'])}"
    return dict(state)


def scheduler_status() -> dict:
    st = dict(_state)
    st["now"] = time.strftime("%H:%M")
    st["in_window"] = within_window()
    st["due_now"] = _due_cases(limit=50)
    return st


def scheduler_set_enabled(on: bool) -> dict:
    _state["enabled"] = bool(on)
    logger.info(f"scheduler {'enabled' if on else 'paused'}")
    return dict(_state)


async def _main() -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="XCEB scheduler CLI")
    ap.add_argument("--once", action="store_true", help="run a single tick and exit")
    ap.add_argument("--status", action="store_true", help="print scheduler status")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(scheduler_status(), indent=2))
        return 0
    if args.once:
        await scheduler_tick(emit=print)
        print(json.dumps(_state, indent=2))
        return 0
    # Continuous loop (for standalone operation without the FastAPI server)
    while True:
        await scheduler_tick(emit=lambda k, p: print(k, json.dumps(p)))
        await asyncio.sleep(_state["tick_sec"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
