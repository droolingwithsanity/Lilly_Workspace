#!/usr/bin/env python3
"""
Admin Bot — Background monitoring module for Lilly AI.

Scans system health, code quality, and improvement opportunities.
Posts suggestions to the admin UI via the suggestion system in lilly_ai.py.

Can be imported by lilly_ai.py for the background loop, or run standalone
for testing: python admin_bot.py --scan
"""

import os, sys, json, time, shutil, subprocess, asyncio, logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("admin_bot")

WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", str(Path(__file__).parent)))
SENSOR_SERVER_URL = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")


# ── Scan Results ──────────────────────────────────────────────────────


def scan_system_health() -> list[dict]:
    """Check system health: disk, Docker, sensor server."""
    findings = []

    # Disk space
    try:
        stat = shutil.disk_usage(str(WORKSPACE))
        pct_free = stat.free / stat.total * 100
        if pct_free < 10:
            findings.append(
                {
                    "title": "Low disk space",
                    "description": f"Only {pct_free:.1f}% disk free ({stat.free // (1024**3)} GB). Consider cleanup.",
                    "category": "health",
                    "priority": "high" if pct_free < 5 else "medium",
                    "metadata": {
                        "pct_free": round(pct_free, 1),
                        "free_gb": round(stat.free / (1024**3), 1),
                    },
                }
            )
    except Exception:
        pass

    # Docker containers
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}: {{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.strip()
        if output:
            stopped = [line for line in output.split("\n") if "Up" not in line]
            if stopped:
                findings.append(
                    {
                        "title": "Docker containers stopped",
                        "description": f"Stopped containers: {', '.join(stopped[:5])}",
                        "category": "health",
                        "priority": "high",
                        "metadata": {"stopped": stopped},
                    }
                )
    except Exception:
        pass

    # Memory usage
    try:
        result = subprocess.run(
            ["free", "-m"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.split("\n"):
            if line.startswith("Mem:"):
                parts = line.split()
                total, used, available = int(parts[1]), int(parts[2]), int(parts[6])
                pct_used = used / total * 100
                if pct_used > 90:
                    findings.append(
                        {
                            "title": "High memory usage",
                            "description": f"Memory {pct_used:.0f}% used ({used}MB / {total}MB). Available: {available}MB.",
                            "category": "health",
                            "priority": "high",
                            "metadata": {
                                "pct_used": round(pct_used, 1),
                                "available_mb": available,
                            },
                        }
                    )
                break
    except Exception:
        pass

    return findings


def scan_code_quality() -> list[dict]:
    """Scan for TODO/FIXME/HACK comments, unused imports, etc."""
    findings = []
    todo_files = {
        "lilly_ai.py": 0,
        "phone_broker.py": 0,
        "admin_bot.py": 0,
        "termux_sensor_server.py": 0,
    }

    for filename in todo_files:
        filepath = WORKSPACE / filename
        if not filepath.exists():
            continue
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
            todo_count = (
                content.count("TODO") + content.count("FIXME") + content.count("HACK")
            )
            todo_files[filename] = todo_count
        except Exception:
            pass

    total_todos = sum(todo_files.values())
    if total_todos > 30:
        top_files = sorted(todo_files.items(), key=lambda x: x[1], reverse=True)[:3]
        details = ", ".join(f"{f}: {c}" for f, c in top_files if c > 0)
        findings.append(
            {
                "title": f"{total_todos} TODO/FIXME/HACK comments in codebase",
                "description": f"Top files: {details}. Consider addressing outstanding items.",
                "category": "optimization",
                "priority": "low",
                "metadata": {"total": total_todos, "per_file": todo_files},
            }
        )

    return findings


def scan_upgrades() -> list[dict]:
    """Check for outdated models, dependencies, etc."""
    findings = []

    # YOLO model age
    for model_name in ["yolov8n.pt", "yolov8s.pt"]:
        model_path = WORKSPACE / model_name
        if model_path.exists():
            age_days = (time.time() - model_path.stat().st_mtime) / 86400
            if age_days > 90:
                findings.append(
                    {
                        "title": f"{model_name} may be outdated",
                        "description": f"Model is {age_days:.0f} days old. Check for newer YOLO versions.",
                        "category": "upgrade",
                        "priority": "low",
                        "metadata": {"model": model_name, "age_days": round(age_days)},
                    }
                )

    # Python dependency check (basic)
    req_file = WORKSPACE / "requirements.txt"
    if req_file.exists():
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                outdated = json.loads(result.stdout)
                if len(outdated) > 5:
                    top = [
                        f"{p['name']} ({p['version']}→{p['latest_version']})"
                        for p in outdated[:5]
                    ]
                    findings.append(
                        {
                            "title": f"{len(outdated)} Python packages outdated",
                            "description": f"Top: {', '.join(top)}",
                            "category": "upgrade",
                            "priority": "medium",
                            "metadata": {"count": len(outdated), "top": top},
                        }
                    )
        except Exception:
            pass

    return findings


def scan_security() -> list[dict]:
    """Basic security checks."""
    findings = []

    # Check for exposed .env files
    env_file = WORKSPACE / ".env"
    if env_file.exists():
        try:
            content = env_file.read_text(encoding="utf-8", errors="replace")
            # Check if API keys look real (basic heuristic)
            real_keys = sum(
                1
                for line in content.split("\n")
                if line.strip()
                and not line.startswith("#")
                and len(line.split("=", 1)[-1].strip().strip('"').strip("'")) > 20
            )
            if real_keys > 3:
                findings.append(
                    {
                        "title": ".env file contains multiple API keys",
                        "description": f"Found {real_keys} potential API keys. Ensure .env is in .gitignore.",
                        "category": "security",
                        "priority": "medium",
                    }
                )
        except Exception:
            pass

    # Check git status for secrets
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(WORKSPACE),
        )
        staged = result.stdout.strip().split("\n") if result.stdout.strip() else []
        secret_patterns = [".env", "credentials", "secret", "token", "key"]
        for f in staged:
            if any(p in f.lower() for p in secret_patterns):
                findings.append(
                    {
                        "title": f"Potentially sensitive file staged: {f}",
                        "description": "A file with a sensitive name is staged for commit. Verify this is intended.",
                        "category": "security",
                        "priority": "high",
                        "metadata": {"file": f},
                    }
                )
    except Exception:
        pass

    return findings


def run_full_scan() -> list[dict]:
    """Run all scan categories and return combined findings."""
    all_findings = []
    all_findings.extend(scan_system_health())
    all_findings.extend(scan_code_quality())
    all_findings.extend(scan_upgrades())
    all_findings.extend(scan_security())
    return all_findings


# ── Standalone runner ─────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Admin Bot scanner")
    parser.add_argument(
        "--scan", action="store_true", help="Run full scan and print results"
    )
    parser.add_argument("--health", action="store_true", help="Run health scan only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    if args.scan or args.health or not any([args.scan, args.health]):
        findings = run_full_scan() if args.scan else scan_system_health()

        if args.json:
            print(json.dumps(findings, indent=2, default=str))
        else:
            if not findings:
                print("✅ No issues found — system looks healthy!")
            else:
                print(f"\n🔍 Found {len(findings)} item(s):\n")
                for i, f in enumerate(findings, 1):
                    icon = {
                        "critical": "🔴",
                        "high": "🟠",
                        "medium": "🟡",
                        "low": "🟢",
                    }.get(f["priority"], "⚪")
                    print(f"  {icon} [{f['category'].upper()}] {f['title']}")
                    print(f"     {f['description']}\n")
