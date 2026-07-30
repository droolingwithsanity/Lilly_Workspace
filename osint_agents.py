#!/usr/bin/env python3
"""
Lilly OSINT Agent System
========================
Routes OSINT requests to the right tool, runs it, and formats results.
Each category has its own agent function that knows the best tools to use.

Usage from lilly_ai.py:
    from osint_agents import run_osint_agent
    result = await run_osint_agent("people", "john@example.com")
"""

import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Optional


# ── Agent Registry ──────────────────────────────────────────────────────────

OSINT_AGENTS = {
    "people": {
        "name": "People Intelligence",
        "description": "Find people by name, username, email, or phone",
        "tools": ["sherlock", "maigret", "holehe", "ghunt", "idcrawl", "truepeoplesearch"],
    },
    "email": {
        "name": "Email Intelligence",
        "description": "Investigate email addresses — breaches, registrations, owner info",
        "tools": ["holehe", "ghunt", "epieos", "haveibeenpwned", "emailrep"],
    },
    "username": {
        "name": "Username Intelligence",
        "description": "Track usernames across 3000+ platforms",
        "tools": ["sherlock", "maigret", "whatsmyname"],
    },
    "phone": {
        "name": "Phone Intelligence",
        "description": "Look up phone number owner, carrier, and location",
        "tools": ["phoneinfoga", "idcrawl", "nuwber", "truecaller"],
    },
    "domain": {
        "name": "Domain & Infrastructure",
        "description": "Recon on domains, IPs, SSL certs, DNS, exposed services",
        "tools": ["shodan", "censys", "crt", "whois", "securitytrails", "virustotal"],
    },
    "social_media": {
        "name": "Social Media Intelligence",
        "description": "Search across social platforms — Twitter, Reddit, LinkedIn, Instagram, Telegram",
        "tools": ["sherlock", "maigret", "instaloader"],
    },
    "security": {
        "name": "Threat Intelligence",
        "description": "Check threats, malware, breaches, and reputation",
        "tools": ["virustotal", "abuseipdb", "urlscan", "pulsedive", "haveibeenpwned"],
    },
    "geo": {
        "name": "Geolocation & Mapping",
        "description": "Find locations, verify images, check sun position, satellite imagery",
        "tools": ["suncalc", "geonames", "mapchecking", "nasa_worldview"],
    },
    "corporate": {
        "name": "Corporate Intelligence",
        "description": "Company research, ownership, offshore leaks, registrations",
        "tools": ["opencorporates", "icij_leaks", "gleif", "opensecrets"],
    },
    "crypto": {
        "name": "Crypto Intelligence",
        "description": "Trace wallets, check scam reports, blockchain analysis",
        "tools": ["etherscan", "blockchain", "chainabuse"],
    },
    "archival": {
        "name": "Archival & Historical",
        "description": "Wayback Machine, archived pages, evidence preservation",
        "tools": ["wayback", "archive_today", "waybackpy"],
    },
    "media": {
        "name": "Media Monitoring",
        "description": "News search, fact checking, misinformation detection",
        "tools": ["gdelt", "snopes", "factcheck"],
    },
}


# ── CLI Runner ──────────────────────────────────────────────────────────────

async def _run_cli(cmd: list[str], timeout: int = 60) -> tuple[str, int]:
    """Run a CLI command and return (stdout, returncode)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace").strip()
        return output, proc.returncode or 0
    except asyncio.TimeoutError:
        return f"Command timed out after {timeout}s", -1
    except FileNotFoundError:
        return f"Command '{cmd[0]}' not found", -2
    except Exception as e:
        return f"Error: {e}", -3


# ── Individual Tool Runners ─────────────────────────────────────────────────

async def run_sherlock(target: str) -> str:
    """Run Sherlock for username lookup across 400+ sites."""
    output, rc = await _run_cli(
        ["sherlock", target, "--timeout", "10", "--print-found"],
        timeout=120
    )
    if rc == 0 and output:
        lines = [l for l in output.split("\n") if "[+]`" in l or "https:" in l]
        if lines:
            return f"Sherlock found {len(lines)} accounts for '{target}':\n" + "\n".join(lines[:20])
        return f"Sherlock: no accounts found for '{target}'"
    return f"Sherlock scan complete for '{target}'"


async def run_maigret(target: str) -> str:
    """Run Maigret for username dossier across 3000+ sites."""
    output, rc = await _run_cli(
        ["maigret", target, "--timeout", "10", "--json", "-o", f"/tmp/maigret_{target}.json"],
        timeout=180
    )
    # Try to read the JSON report
    report_path = Path(f"/tmp/maigret_{target}.json")
    if report_path.exists():
        try:
            data = json.loads(report_path.read_text())
            sites = data.get("sites", {})
            found = sum(1 for s in sites.values() if s.get("status") == "OK")
            return f"Maigret dossier for '{target}': {found} sites confirmed, {len(sites)} checked.\nReport: /tmp/maigret_{target}.json"
        except Exception:
            pass
    return f"Maigret scan complete for '{target}'"


async def run_holehe(target: str) -> str:
    """Run Holehe to check which sites an email is registered on."""
    # Holehe is used as a Python library, not CLI
    script = f'''
import asyncio
from holehe.modules import *
import holehe
async def check():
    email = "{target}"
    results = await holehe.get_emails(email)
    return results
print(asyncio.run(check()))
'''
    output, rc = await _run_cli(["python3", "-c", script], timeout=120)
    if output:
        return f"Holehe results for '{target}':\n{output[:2000]}"
    return f"Holehe scan complete for '{target}'"


async def run_ghunt(target: str) -> str:
    """Run GHunt for Google account investigation."""
    output, rc = await _run_cli(
        ["ghunt", "email", target],
        timeout=60
    )
    if output:
        return f"GHunt results for '{target}':\n{output[:2000]}"
    return f"GHunt scan complete for '{target}'"


async def run_waybackpy(target: str) -> str:
    """Query Wayback Machine for archived snapshots."""
    try:
        import waybackpy
        url = waybackpy.Url(target)
        oldest = url.oldest()
        newest = url.newest()
        total = url.total_snapshots()
        return f"Wayback Machine for '{target}':\nOldest: {oldest}\nNewest: {newest}\nTotal snapshots: {total}"
    except Exception as e:
        return f"Wayback query failed: {e}"


async def run_instaloader(target: str) -> str:
    """Download Instagram profile metadata."""
    output, rc = await _run_cli(
        ["instaloader", "--no-videos", "--no-captions", "--count", "12", "--dirname-pattern", f"/tmp/insta_{target}", "--", target],
        timeout=60
    )
    return f"Instaloader: fetched profile data for '{target}'"


# ── Category Agent Router ───────────────────────────────────────────────────

CATEGORY_RUNNERS = {
    "people": lambda t: run_sherlock(t),
    "username": lambda t: run_maigret(t),
    "email": lambda t: run_holehe(t),
    "social_media": lambda t: run_sherlock(t),
    "archival": lambda t: run_waybackpy(t),
}


async def run_osint_agent(category: str, target: str) -> str:
    """
    Main entry point: run the appropriate OSINT agent for a category + target.

    Delegates to osint_engine.investigate for the full autonomous pipeline.
    Falls back to individual CLI tool runners if the engine is unavailable.

    Args:
        category: One of the OSINT_AGENTS keys (people, email, username, etc.)
        target: The search target (email, username, domain, phone, etc.)

    Returns:
        Formatted string with results.
    """
    # Prefer the full investigation engine — it runs tools in parallel and
    # returns a compiled report with all findings.
    try:
        from osint_engine import investigate
        return await investigate(category, target=target, name=target)
    except ImportError:
        pass

    # Legacy fallback: individual CLI runners for specific categories
    agent = OSINT_AGENTS.get(category)
    if not agent:
        return f"Unknown OSINT category: {category}. Available: {', '.join(OSINT_AGENTS.keys())}"

    runner = CATEGORY_RUNNERS.get(category)
    if runner:
        return await runner(target)

    # Default: return tool suggestions
    tools = ", ".join(agent["tools"][:5])
    return f"OSINT Agent [{agent['name']}] ready for '{target}'. Recommended tools: {tools}."


def list_osint_agents() -> str:
    """List all available OSINT agents."""
    lines = ["Lilly OSINT Agents:"]
    for key, agent in OSINT_AGENTS.items():
        tools = ", ".join(agent["tools"][:3])
        lines.append(f"  {key}: {agent['description']} (tools: {tools})")
    return "\n".join(lines)


# ── Standalone test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(list_osint_agents())
    print()
    print("Testing waybackpy...")
    result = asyncio.run(run_waybackpy("https://example.com"))
    print(result)
