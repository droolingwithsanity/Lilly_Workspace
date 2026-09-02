#!/usr/bin/env python3
"""Extract overlay_local.html from an APK and diff against the current app asset.

Usage:
    python3 compare_overlay_apk.py /path/to/lilly-overlay-v6.6-debug.apk

Prints hash/size for each known version and a structured diff summary
(IDs/classes/CSS hints) so we can tell what "v6.6's overlay" had that
the current one doesn't.
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RAW_NAME = "res/raw/overlay_local.html"

CURRENT = Path(
    "/home/labhrasd/Lilly_Workspace/hitomi-android/"
    "app/src/main/res/raw/overlay_local.html"
)
KNOWN = {
    "v6.9.1  (builds/lilly-overlay-debug.apk)": "/tmp/opencode/v6.9.1-overlay_local.html",
    "v6.14.0 (builds/lilly-overlay-v6.14.0-overlay-debug.apk)": "/tmp/opencode/v6.14.0-overlay_local.html",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def extract_from_apk(apk: Path) -> Path:
    with zipfile.ZipFile(apk) as z:
        if RAW_NAME not in z.namelist():
            sys.exit(f"{RAW_NAME} not in {apk}")
        tmp = Path(tempfile.mkdtemp()) / "target.html"
        tmp.write_bytes(z.read(RAW_NAME))
        return tmp


def structure_stats(p: Path) -> dict:
    html = p.read_text(errors="replace")
    return {
        "lines": len(html.splitlines()),
        "bytes": len(html),
        "ids": sorted(
            {x for x in __import__("re").findall(r'id=["\']([^"\']+)["\']', html)}
        ),
        "title": next(
            iter(__import__("re").findall(r"<title>([^<]*)</title>", html)), None
        ),
        "glass": "glass" in html.lower(),  # "frosty glass" theme marker
        "svelte": "overlay" in html and ("<style" in html.lower()),
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <path-to-apk>")
    apk = Path(sys.argv[1]).expanduser()
    if not apk.exists():
        raise SystemExit(f"not found: {apk}")

    target = extract_from_apk(apk)
    target_stats = structure_stats(target)

    print(f"\n=== {apk.name} ===")
    print(
        f"  sha256: {sha(target):>16}  {target_stats['bytes']} bytes  "
        f"{target_stats['lines']} lines  title={target_stats['title']}  "
        f"glass={target_stats['glass']}"
    )
    print(f"  ids ({len(target_stats['ids'])}): {' '.join(target_stats['ids'])}")

    print("\n=== current app asset ===")
    cur_stats = structure_stats(CURRENT)
    print(
        f"  sha256: {sha(CURRENT):>16}  {cur_stats['bytes']} bytes  "
        f"{cur_stats['lines']} lines  title={cur_stats['title']}  "
        f"glass={cur_stats['glass']}"
    )

    tid, cid = set(target_stats["ids"]), set(cur_stats["ids"])
    print("\n  IDs only in v6.6:", " ".join(sorted(tid - cid)) or "(none)")
    print("  IDs only in current:", " ".join(sorted(cid - tid)) or "(none)")

    for label, path in KNOWN.items():
        if Path(path).exists():
            k = structure_stats(Path(path))
            match = "SAME" if sha(Path(path)) == sha(target) else "diff"
            print(
                f"\n  {label}: {sha(Path(path))} {k['bytes']}b "
                f"{k['lines']}ln glass={k['glass']}  [{match}]"
            )

    print(
        "\n(If wineskins match near-identical, the asset was stable across 6.x "
        "and the 'right' look is the 6.x-era theme, pre-frosty-glass.)"
    )


if __name__ == "__main__":
    main()
