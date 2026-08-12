#!/usr/bin/env python3
"""
Lilly OSINT Investigation Engine
=================================
Autonomous multi-tool pipeline. Runs tools in parallel, aggregates results,
returns a compiled report directly in chat. No browser — everything server-side.

Usage:
    from osint_engine import investigate
    report = await investigate("person", name="John Smith", location="Toronto")
    report = await investigate("email", email="john@example.com")
"""

import asyncio
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import httpx


# ── Data Models ─────────────────────────────────────────────────────────────


@dataclass
class IntelFinding:
    source: str
    category: str  # "username", "email", "social", "breach", "domain", etc.
    data: str
    url: str = ""
    confidence: float = 0.8


@dataclass
class InvestigationReport:
    target: str
    category: str
    started: float = field(default_factory=time.time)
    findings: list[IntelFinding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(
        self,
        source: str,
        category: str,
        data: str,
        url: str = "",
        confidence: float = 0.8,
    ):
        if data and data.strip():
            self.findings.append(
                IntelFinding(
                    source=source,
                    category=category,
                    data=data.strip(),
                    url=url,
                    confidence=confidence,
                )
            )

    def has_findings(self) -> bool:
        return len(self.findings) > 0

    def elapsed(self) -> float:
        return round(time.time() - self.started, 1)


# ── HTTP Client Pool ────────────────────────────────────────────────────────

_http: Optional[httpx.AsyncClient] = None


async def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "LillyOSINT/1.0"},
        )
    return _http


async def _fetch_json(
    url: str, params: Optional[dict] = None, headers: Optional[dict] = None
) -> dict:
    """Fetch JSON from a URL. Returns {} on failure."""
    try:
        c = await _client()
        r = await c.get(url, params=params, headers=headers or {})
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


async def _fetch_text(url: str, params: Optional[dict] = None) -> str:
    """Fetch text from a URL. Returns empty string on failure."""
    try:
        c = await _client()
        r = await c.get(url, params=params)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""


# ── Individual Intel Collectors ─────────────────────────────────────────────


async def collect_username_sherlock(username: str, report: InvestigationReport):
    """Sherlock: find username across 400+ sites via CLI."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sherlock",
            username,
            "--timeout",
            "8",
            "--print-found",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
        output = stdout.decode(errors="replace")
        matches = re.findall(r"\[+\]\s+(\S+):\s+(https?://\S+)", output)
        if matches:
            sites = [f"{name}" for name, _ in matches[:30]]
            report.add(
                "Sherlock",
                "username",
                f"Found on {len(matches)} sites: {', '.join(sites)}",
            )
            # Add individual URLs
            for name, url in matches[:10]:
                report.add("Sherlock", "social", f"{name}: {url}", url=url)
        else:
            report.add("Sherlock", "username", f"No accounts found for '{username}'")
    except asyncio.TimeoutError:
        report.errors.append("Sherlock timed out")
    except Exception as e:
        report.errors.append(f"Sherlock: {e}")


async def collect_username_maigret(username: str, report: InvestigationReport):
    """Maigret: build dossier from 3000+ sites."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "maigret",
            username,
            "--timeout",
            "8",
            "--json",
            "-o",
            f"/tmp/maigret_{username}.json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        # Read JSON report
        from pathlib import Path

        report_path = Path(f"/tmp/maigret_{username}.json")
        if report_path.exists():
            data = json.loads(report_path.read_text())
            sites = data.get("sites", {})
            found = {k: v for k, v in sites.items() if v.get("status") == "OK"}
            if found:
                report.add(
                    "Maigret",
                    "username",
                    f"Confirmed on {len(found)} sites out of {len(sites)} checked",
                )
                for site_name, site_data in list(found.items())[:15]:
                    url = site_data.get("url", "")
                    report.add("Maigret", "social", f"{site_name}", url=url)
    except asyncio.TimeoutError:
        report.errors.append("Maigret timed out")
    except Exception as e:
        report.errors.append(f"Maigret: {e}")


async def collect_email_holehe(email: str, report: InvestigationReport):
    """Holehe: check email registrations across 120+ sites."""
    try:
        script = f'''
import asyncio, json
from holehe.modules import *
import holehe
async def check():
    return await holehe.get_emails("{email}")
results = asyncio.run(check())
print(json.dumps(results))
'''
        proc = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = stdout.decode(errors="replace").strip()
        if output:
            try:
                data = json.loads(output)
                if isinstance(data, list):
                    found = [item for item in data if item.get("exists") is True]
                    if found:
                        sites = [item.get("name", "unknown") for item in found[:20]]
                        report.add(
                            "Holehe",
                            "email",
                            f"Registered on {len(found)} sites: {', '.join(sites)}",
                        )
                    else:
                        report.add("Holehe", "email", "No site registrations found")
            except json.JSONDecodeError:
                if "True" in output or "exists" in output:
                    report.add("Holehe", "email", f"Email found on multiple sites")
    except asyncio.TimeoutError:
        report.errors.append("Holehe timed out")
    except Exception as e:
        report.errors.append(f"Holehe: {e}")


async def collect_email_haveibeenpwned(email: str, report: InvestigationReport):
    """HIBP: check if email appeared in data breaches."""
    try:
        c = await _client()
        r = await c.get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
            headers={"hibp-api-key": ""},
            timeout=10.0,
        )
        if r.status_code == 200:
            breaches = r.json()
            names = [b.get("Name", "") for b in breaches[:10]]
            report.add(
                "HIBP",
                "breach",
                f"Found in {len(breaches)} breaches: {', '.join(names)}",
            )
        elif r.status_code == 404:
            report.add("HIBP", "breach", "Not found in any known breaches")
    except Exception as e:
        report.errors.append(f"HIBP: {e}")


async def collect_email_rep(email: str, report: InvestigationReport):
    """EmailRep: email reputation and risk assessment."""
    try:
        data = await _fetch_json(f"https://emailrep.io/{email}")
        if data:
            reputation = data.get("reputation", "unknown")
            suspicious = data.get("suspicious", False)
            details = data.get("details", {})
            providers = details.get("providers", [])
            report.add(
                "EmailRep",
                "email",
                f"Reputation: {reputation}, Suspicious: {suspicious}, "
                f"Provider: {', '.join(providers[:3]) if providers else 'unknown'}",
            )
    except Exception as e:
        report.errors.append(f"EmailRep: {e}")


async def collect_domain_shodan(domain: str, report: InvestigationReport):
    """Shodan: exposed services and ports."""
    try:
        data = await _fetch_json(
            f"https://api.shodan.io/dns/domain/{domain}", params={"key": ""}
        )
        if data:
            records = data.get("data", [])
            ips = list(set(r.get("data", "") for r in records if r.get("type") == "A"))
            if ips:
                report.add("Shodan", "infrastructure", f"IPs: {', '.join(ips[:5])}")
            subdomains = list(
                set(r.get("subdomain", "") for r in records if r.get("subdomain"))
            )
            if subdomains:
                report.add(
                    "Shodan",
                    "infrastructure",
                    f"Subdomains: {', '.join(subdomains[:10])}",
                )
    except Exception as e:
        report.errors.append(f"Shodan: {e}")


async def collect_domain_crtsh(domain: str, report: InvestigationReport):
    """crt.sh: SSL certificate transparency logs."""
    try:
        text = await _fetch_text(f"https://crt.sh/?q=%25.{domain}&output=json")
        if text:
            certs = json.loads(text)
            names = set()
            for cert in certs:
                name = cert.get("name_value", "")
                if name and "*" not in name:
                    names.add(name)
            if names:
                report.add(
                    "crt.sh",
                    "infrastructure",
                    f"Certificate names: {', '.join(sorted(names)[:15])}",
                )
    except Exception as e:
        report.errors.append(f"crt.sh: {e}")


async def collect_domain_whois(domain: str, report: InvestigationReport):
    """WHOIS lookup via web API."""
    try:
        data = await _fetch_json(f"https://rdap.org/domain/{domain}")
        if data:
            events = data.get("events", [])
            for ev in events:
                if ev.get("eventAction") == "registration":
                    report.add(
                        "WHOIS",
                        "domain",
                        f"Registered: {ev.get('eventDate', 'unknown')}",
                    )
            entities = data.get("entities", [])
            for ent in entities:
                handle = ent.get("handle", "")
                if handle:
                    report.add("WHOIS", "domain", f"Registrar: {handle}")
                    break
    except Exception as e:
        report.errors.append(f"WHOIS: {e}")


async def collect_phone_infoga(phone: str, report: InvestigationReport):
    """PhoneInfoga: phone number OSINT."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            f'''
import json
try:
    from phoneinfoga import scan
    results = scan("{phone}")
    print(json.dumps(results))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
''',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode(errors="replace").strip()
        if output:
            try:
                data = json.loads(output)
                if not data.get("error"):
                    report.add(
                        "PhoneInfoga", "phone", f"Phone data retrieved for {phone}"
                    )
            except json.JSONDecodeError:
                pass
    except Exception as e:
        report.errors.append(f"PhoneInfoga: {e}")


async def collect_social_web_search(target: str, report: InvestigationReport):
    """Web search for social media profiles."""
    try:
        query = urllib.parse.quote(
            f'"{target}" site:linkedin.com OR site:twitter.com OR site:facebook.com OR site:instagram.com'
        )
        c = await _client()
        r = await c.get(f"https://html.duckduckgo.com/html/?q={query}", timeout=10.0)
        if r.status_code == 200:
            # Extract links from DuckDuckGo HTML results
            links = re.findall(
                r'href="(https?://(?:linkedin|twitter|facebook|instagram)\.com/[^"]+)"',
                r.text,
            )
            if links:
                report.add("WebSearch", "social", f"Found {len(links)} social profiles")
                for url in links[:8]:
                    report.add("WebSearch", "social", url, url=url)
    except Exception as e:
        report.errors.append(f"WebSearch: {e}")


async def collect_geolocation_name(name: str, report: InvestigationReport):
    """GeoNames: geographic entity lookup."""
    try:
        data = await _fetch_json(
            "http://api.geonames.org/searchJSON",
            params={"q": name, "maxRows": 5, "username": "demo"},
        )
        geonames = data.get("geonames", [])
        if geonames:
            for g in geonames[:3]:
                country = g.get("countryName", "")
                lat = g.get("lat", "")
                lng = g.get("lng", "")
                pop = g.get("population", "")
                report.add(
                    "GeoNames",
                    "location",
                    f"{g.get('name', name)}: {country} ({lat},{lng}) pop={pop}",
                )
    except Exception as e:
        report.errors.append(f"GeoNames: {e}")


async def collect_company_opencorporates(name: str, report: InvestigationReport):
    """OpenCorporates: company search."""
    try:
        data = await _fetch_json(
            f"https://api.opencorporates.com/v0.4/companies/search",
            params={"q": name, "per_page": 5},
        )
        companies = data.get("results", {}).get("companies", [])
        if companies:
            for co in companies[:5]:
                c = co.get("company", {})
                jurisdiction = c.get("jurisdiction_code", "")
                status = c.get("current_status", "")
                report.add(
                    "OpenCorporates",
                    "corporate",
                    f"{c.get('name', '')} ({jurisdiction}) — {status}",
                )
    except Exception as e:
        report.errors.append(f"OpenCorporates: {e}")


async def collect_crypto_etherscan(address: str, report: InvestigationReport):
    """Etherscan: Ethereum wallet lookup."""
    try:
        data = await _fetch_json(
            f"https://api.etherscan.io/api",
            params={
                "module": "account",
                "action": "balance",
                "address": address,
                "tag": "latest",
            },
        )
        result = data.get("result", "")
        if result and result != "0":
            balance_eth = int(result) / 1e18
            report.add("Etherscan", "crypto", f"ETH Balance: {balance_eth:.4f}")
    except Exception as e:
        report.errors.append(f"Etherscan: {e}")


# ── Investigation Workflows ─────────────────────────────────────────────────


async def _investigate_person(
    name: str,
    location: str = "",
    username: str = "",
    email: str = "",
    photo_url: str = "",
) -> InvestigationReport:
    """Full person investigation — runs all available tools in parallel."""
    report = InvestigationReport(target=name, category="person")

    # Determine search targets
    search_username = (
        username or name.split()[0].lower() + name.split()[-1].lower()
        if " " in name
        else name.lower()
    )
    search_email = email
    search_queries = [name]
    if location:
        search_queries.append(f"{name} {location}")

    # Run all collectors in parallel
    tasks = [
        collect_username_sherlock(search_username, report),
        collect_username_maigret(search_username, report),
        collect_social_web_search(" ".join(search_queries), report),
    ]

    if search_email:
        tasks.extend(
            [
                collect_email_holehe(search_email, report),
                collect_email_haveibeenpwned(search_email, report),
                collect_email_rep(search_email, report),
            ]
        )

    if location:
        tasks.append(collect_geolocation_name(location, report))

    await asyncio.gather(*tasks, return_exceptions=True)
    return report


async def _investigate_email(email: str) -> InvestigationReport:
    """Full email investigation."""
    report = InvestigationReport(target=email, category="email")

    await asyncio.gather(
        collect_email_holehe(email, report),
        collect_email_haveibeenpwned(email, report),
        collect_email_rep(email, report),
        collect_social_web_search(email, report),
        return_exceptions=True,
    )
    return report


async def _investigate_username(username: str) -> InvestigationReport:
    """Full username investigation."""
    report = InvestigationReport(target=username, category="username")

    await asyncio.gather(
        collect_username_sherlock(username, report),
        collect_username_maigret(username, report),
        collect_social_web_search(username, report),
        return_exceptions=True,
    )
    return report


async def _investigate_domain(domain: str) -> InvestigationReport:
    """Full domain investigation."""
    report = InvestigationReport(target=domain, category="domain")

    await asyncio.gather(
        collect_domain_shodan(domain, report),
        collect_domain_crtsh(domain, report),
        collect_domain_whois(domain, report),
        collect_social_web_search(domain, report),
        return_exceptions=True,
    )
    return report


async def _investigate_phone(phone: str) -> InvestigationReport:
    """Full phone investigation."""
    report = InvestigationReport(target=phone, category="phone")

    await asyncio.gather(
        collect_phone_infoga(phone, report),
        collect_social_web_search(phone, report),
        return_exceptions=True,
    )
    return report


async def _investigate_company(name: str) -> InvestigationReport:
    """Full company investigation."""
    report = InvestigationReport(target=name, category="company")

    await asyncio.gather(
        collect_company_opencorporates(name, report),
        collect_social_web_search(f'"{name}" company', report),
        collect_domain_whois(name.lower().replace(" ", "") + ".com", report),
        return_exceptions=True,
    )
    return report


async def _investigate_crypto(address: str) -> InvestigationReport:
    """Full crypto wallet investigation."""
    report = InvestigationReport(target=address, category="crypto")

    await asyncio.gather(
        collect_crypto_etherscan(address, report),
        collect_social_web_search(address, report),
        return_exceptions=True,
    )
    return report


# ── Report Formatter ────────────────────────────────────────────────────────


def format_report(report: InvestigationReport) -> str:
    """Format an InvestigationReport into a clean chat-ready report."""
    lines = []
    lines.append(f"--- Investigation Report ---")
    lines.append(f"Target: {report.target}")
    lines.append(f"Category: {report.category.title()}")
    lines.append(f"Tools used: {len(set(f.source for f in report.findings))}")
    lines.append(f"Findings: {len(report.findings)}")
    lines.append(f"Time: {report.elapsed()}s")
    lines.append("")

    # Group findings by category
    categories = {}
    for f in report.findings:
        cat = f.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(f)

    cat_order = [
        "username",
        "social",
        "email",
        "breach",
        "phone",
        "domain",
        "infrastructure",
        "location",
        "corporate",
        "crypto",
    ]
    for cat in cat_order:
        if cat in categories:
            findings = categories[cat]
            lines.append(f"[{cat.upper()}]")
            for f in findings[:8]:
                source_tag = f"({f.source})" if f.source else ""
                lines.append(f"  {f.data} {source_tag}")
            if len(findings) > 8:
                lines.append(f"  ... +{len(findings) - 8} more findings")
            lines.append("")

    # Remaining categories not in the predefined order
    for cat, findings in categories.items():
        if cat not in cat_order:
            lines.append(f"[{cat.upper()}]")
            for f in findings[:5]:
                source_tag = f"({f.source})" if f.source else ""
                lines.append(f"  {f.data} {source_tag}")
            lines.append("")

    if report.errors:
        lines.append(f"[TOOLS WITH ERRORS]")
        for e in report.errors[:3]:
            lines.append(f"  {e}")
        lines.append("")

    if not report.has_findings():
        lines.append(
            "No findings across any tool. The target may not have a public digital footprint."
        )

    return "\n".join(lines)


# ── Main Entry Point ────────────────────────────────────────────────────────


async def investigate(category: str, **kwargs) -> str:
    """
    Main entry point. Runs the investigation and returns a formatted report.

    Core categories:
        person       — name, location, username, email, photo_url
        email        — email
        username     — username
        domain       — domain
        phone        — phone
        company      — name
        crypto       — address

    Alias categories (mapped to the closest workflow):
        people       → person
        infrastructure, ip, subdomain, ssl, whois, wifi, url_scan → domain
        social_media, twitter, reddit, instagram, linkedin, youtube,
        telegram, discord → username (social search)
        security, threat, breach, dark_web, leaked_data → email (breach focus)
        archival     → domain (archive scan)
        geo          → person (with geolocation)
        corporate    → company
        media        → person (news search)
        image, toolset, comms, info, transport,
        public_records, environment, verification → person (general search)
    """
    target = kwargs.get("target", kwargs.get("name", ""))

    # Canonical category aliases — map skill osint_category to workflow functions
    ALIASES: dict[str, str] = {
        # person variants
        "people": "person",
        # domain/infrastructure variants
        "infrastructure": "domain",
        "ip": "domain",
        "subdomain": "domain",
        "ssl": "domain",
        "whois": "domain",
        "wifi": "domain",
        "url_scan": "domain",
        # social variants — run username search
        "social_media": "username",
        "twitter": "username",
        "reddit": "username",
        "instagram": "username",
        "linkedin": "username",
        "youtube": "username",
        "telegram": "username",
        "discord": "username",
        # security/breach variants — run email investigation
        "security": "email",
        "threat": "email",
        "breach": "email",
        "dark_web": "email",
        "leaked_data": "email",
        # archival — treat as domain
        "archival": "domain",
        # geo — use person with location context
        "geo": "person",
        # corporate — company lookup
        "corporate": "company",
        # catch-all fallbacks: run person/web search
        "media": "person",
        "image": "person",
        "toolset": "person",
        "comms": "person",
        "info": "person",
        "transport": "person",
        "public_records": "person",
        "environment": "person",
        "verification": "person",
    }

    # Resolve alias
    resolved = ALIASES.get(category, category)

    workflow = {
        "person": lambda: _investigate_person(
            name=kwargs.get("name", target),
            location=kwargs.get("location", ""),
            username=kwargs.get("username", ""),
            email=kwargs.get("email", ""),
            photo_url=kwargs.get("photo_url", ""),
        ),
        "email": lambda: _investigate_email(kwargs.get("email", target)),
        "username": lambda: _investigate_username(kwargs.get("username", target)),
        "domain": lambda: _investigate_domain(kwargs.get("domain", target)),
        "phone": lambda: _investigate_phone(kwargs.get("phone", target)),
        "company": lambda: _investigate_company(
            kwargs.get("company", kwargs.get("name", target))
        ),
        "crypto": lambda: _investigate_crypto(
            kwargs.get("address", kwargs.get("wallet", target))
        ),
    }

    fn = workflow.get(resolved)
    if not fn:
        available = list(workflow.keys()) + list(ALIASES.keys())
        return f"Unknown investigation category: {category!r}. Available: {', '.join(sorted(set(available)))}"

    report = await fn()
    return format_report(report)


# ── Standalone test ─────────────────────────────────────────────────────────

if __name__ == "__main__":

    async def test():
        result = await investigate("username", target="test")
        print(result)

    asyncio.run(test())
