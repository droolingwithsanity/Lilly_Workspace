import requests
import ssl
import socket
import os
import re
from datetime import datetime

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("LILLY_MODEL", "tinyllama")


SECURITY_HEADERS = {
    "Strict-Transport-Security": "Enforces HTTPS connections, preventing downgrade attacks",
    "Content-Security-Policy": "Controls which resources can be loaded, preventing XSS",
    "X-Frame-Options": "Prevents clickjacking by controlling iframe embedding",
    "X-Content-Type-Options": "Prevents MIME-type sniffing attacks",
    "Referrer-Policy": "Controls how much referrer information is sent",
    "Permissions-Policy": "Controls which browser APIs and features can be used",
    "X-XSS-Protection": "Ancient XSS filter (largely deprecated but still found)",
}


def analyze_website(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = {
        "url": url,
        "status_code": None,
        "response_time_ms": None,
        "headers": {},
        "security_headers": {},
        "ssl_info": {},
        "tech_stack": [],
        "issues": [],
        "recommendations": [],
        "grade": "C",
        "has_root_access": True,
    }

    try:
        start = datetime.now()
        resp = requests.get(url, timeout=15, allow_redirects=True, headers={
            "User-Agent": "LillyOS-Analyzer/1.0",
        })
        elapsed = (datetime.now() - start).total_seconds() * 1000
        result["status_code"] = resp.status_code
        result["response_time_ms"] = round(elapsed, 1)
        result["headers"] = dict(resp.headers)

        # Check security headers
        for hdr, desc in SECURITY_HEADERS.items():
            val = resp.headers.get(hdr)
            result["security_headers"][hdr] = {
                "present": val is not None,
                "value": val[:100] if val else None,
                "description": desc,
            }

        # Detect tech stack from headers
        tech_hints = []
        server = resp.headers.get("Server", "")
        if server:
            tech_hints.append(server)
        powered = resp.headers.get("X-Powered-By", "")
        if powered:
            tech_hints.extend(p.strip() for p in powered.split(","))

        # Detect from HTML
        html = resp.text[:50000] if resp.text else ""
        tech_patterns = [
            (r'react[.-]?\d*', 'React'),
            (r'vue[.-]?\d*', 'Vue.js'),
            (r'angular[.-]?\d*', 'Angular'),
            (r'jquery[.-]?\d*', 'jQuery'),
            (r'next[.-]?\d*', 'Next.js'),
            (r'nuxt[.-]?\d*', 'Nuxt.js'),
            (r'bootstrap[.-]?\d*', 'Bootstrap'),
            (r'tailwind', 'Tailwind CSS'),
            (r'django', 'Django'),
            (r'flask', 'Flask'),
            (r'fastapi', 'FastAPI'),
            (r'laravel', 'Laravel'),
            (r'wordpress', 'WordPress'),
            (r'wp-content', 'WordPress'),
            (r'drupal', 'Drupal'),
            (r'jekyll', 'Jekyll'),
            (r'hugo', 'Hugo'),
            (r'nginx', 'Nginx'),
            (r'apache', 'Apache'),
            (r'express', 'Express'),
            (r'socket\.io', 'Socket.IO'),
            (r'three\.js', 'Three.js'),
            (r'd3\.js', 'D3.js'),
            (r'chart\.js', 'Chart.js'),
        ]
        for pattern, name in tech_patterns:
            if re.search(pattern, html, re.IGNORECASE):
                if name not in tech_hints:
                    tech_hints.append(name)
        for hint in tech_hints:
            if hint not in result["tech_stack"]:
                result["tech_stack"].append(hint)
        result["tech_stack"] = sorted(set(result["tech_stack"]))

        # Generate issues
        issues = []
        for hdr, info in result["security_headers"].items():
            if not info["present"]:
                severity = "high" if hdr in ("Strict-Transport-Security", "Content-Security-Policy", "X-Frame-Options") else "medium"
                issues.append({
                    "type": "missing_header",
                    "severity": severity,
                    "header": hdr,
                    "message": f"Missing {hdr} header",
                    "description": info["description"],
                    "fix": _fix_header(hdr, url),
                })

        if result["status_code"] and result["status_code"] >= 400:
            issues.append({
                "type": "http_error",
                "severity": "high",
                "message": f"HTTP {result['status_code']} error",
                "description": "The server returned an error status code.",
                "fix": "Check server logs and application health.",
            })

        if result["response_time_ms"] and result["response_time_ms"] > 3000:
            issues.append({
                "type": "slow_response",
                "severity": "medium",
                "message": f"Slow response time ({result['response_time_ms']}ms)",
                "description": "Response time exceeds 3 seconds, impacting user experience.",
                "fix": "Consider caching, CDN, or optimizing backend queries.",
            })

        result["issues"] = issues

        # Compute grade
        missing = sum(1 for h in result["security_headers"].values() if not h["present"])
        error_bonus = 1 if any(i["severity"] == "high" for i in issues) else 0
        grade_map = {0: "A", 1: "A-", 2: "B+", 3: "B", 4: "C+", 5: "C", 6: "D", 7: "F"}
        grade_idx = missing + error_bonus
        result["grade"] = grade_map.get(min(grade_idx, 7), "F")

    except requests.ConnectionError:
        result["issues"].append({
            "type": "connection_error",
            "severity": "critical",
            "message": "Could not connect to the server",
            "description": "The website is unreachable. Check if the server is running and the URL is correct.",
            "fix": "Verify the server is running and accessible from this network.",
        })
        result["grade"] = "F"
    except requests.Timeout:
        result["issues"].append({
            "type": "timeout",
            "severity": "high",
            "message": "Request timed out after 15 seconds",
            "description": "The server took too long to respond.",
            "fix": "Check server load, network latency, and firewall rules.",
        })
        result["grade"] = "F"
    except Exception as e:
        result["issues"].append({
            "type": "error",
            "severity": "high",
            "message": f"Analysis error: {str(e)[:100]}",
            "description": "An unexpected error occurred during analysis.",
            "fix": "Retry the analysis or check the URL format.",
        })
        result["grade"] = "F"

    try:
        hostname = re.sub(r'^https?://', '', url).split('/')[0].split(':')[0]
        cert = ssl.get_server_certificate((hostname, 443))
        from OpenSSL import crypto
        x509 = crypto.load_certificate(crypto.FILETYPE_PEM, cert)
        expiry = x509.get_notAfter().decode()
        expiry_dt = datetime.strptime(expiry, "%Y%m%d%H%M%SZ")
        days_left = (expiry_dt - datetime.utcnow()).days
        result["ssl_info"] = {
            "valid": days_left > 0,
            "expires": expiry_dt.isoformat(),
            "days_left": days_left,
            "issuer": str(x509.get_issuer()),
            "subject": str(x509.get_subject()),
        }
        if days_left < 30:
            result["issues"].append({
                "type": "ssl_expiring",
                "severity": "high" if days_left < 7 else "medium",
                "header": "SSL Certificate",
                "message": f"SSL certificate expires in {days_left} days",
                "description": "The SSL certificate is expiring soon.",
                "fix": f"Renew the SSL certificate before {expiry_dt.strftime('%Y-%m-%d')}.",
            })
    except Exception:
        result["ssl_info"] = {"valid": False, "note": "Could not retrieve SSL certificate"}

    return result


def generate_ai_report(url: str, analysis: dict, has_root_access: bool = True) -> str:
    issues_text = "\n".join(
        f"- [{i['severity'].upper()}] {i['message']}"
        for i in (analysis.get("issues") or [])
    ) or "No issues detected."

    headers_text = "\n".join(
        f"- {hdr}: {'✅ Present' if info['present'] else '❌ Missing'} ({info['description']})"
        for hdr, info in (analysis.get("security_headers") or {}).items()
    )

    tech_text = ", ".join(analysis.get("tech_stack") or ["Unknown"]) or "None detected"

    grade = analysis.get("grade", "N/A")
    ssl_info = analysis.get("ssl_info", {})
    ssl_text = f"Valid: {ssl_info.get('valid', 'N/A')}, Expires: {ssl_info.get('expires', 'N/A')[:10]}, Days left: {ssl_info.get('days_left', 'N/A')}" if ssl_info else "N/A"

    system = (
        "You are Lilly, Chief of Staff AI. Provide a concise, professional website analysis report. "
        "Structure your response with clear sections and actionable recommendations."
    )

    prompt = (
        f"Website Analysis Report for: {url}\n\n"
        f"**Raw Findings:**\n"
        f"- Status: {analysis.get('status_code', 'N/A')}\n"
        f"- Response Time: {analysis.get('response_time_ms', 'N/A')}ms\n"
        f"- Security Grade: {grade}\n"
        f"- SSL: {ssl_text}\n"
        f"- Tech Stack: {tech_text}\n\n"
        f"**Security Headers:**\n{headers_text}\n\n"
        f"**Issues Found:**\n{issues_text}\n\n"
        f"Root access available: {has_root_access}\n\n"
        f"Please provide:\n"
        f"1. **Executive Summary** — brief overview of the site's security posture\n"
        f"2. **Critical Issues** — urgent items requiring immediate attention\n"
        f"3. **Recommendations** — numbered list of actionable fixes\n"
        f"4. **Security Advisory** — if root access is NOT available, provide practical mitigations "
        f"the site owner can implement through their hosting panel, .htaccess, or CMS settings\n"
        f"5. **Overall Assessment** — final verdict with grade"
    )

    try:
        import requests as req
        resp = req.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "system": system,
                   "stream": False, "options": {"num_predict": 1024}},
            timeout=45,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "")
    except Exception:
        pass

    return _fallback_report(analysis, has_root_access)


def _fix_header(header: str, url: str) -> str:
    domain = re.sub(r'^https?://', '', url).split('/')[0]
    fixes = {
        "Strict-Transport-Security": f'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload";',
        "Content-Security-Policy": f'default-src \'self\'; script-src \'self\'; style-src \'self\' \'unsafe-inline\';',
        "X-Frame-Options": f'add_header X-Frame-Options "SAMEORIGIN";',
        "X-Content-Type-Options": f'add_header X-Content-Type-Options "nosniff";',
        "Referrer-Policy": f'add_header Referrer-Policy "strict-origin-when-cross-origin";',
        "Permissions-Policy": f'add_header Permissions-Policy "camera=(), microphone=(), geolocation=()";',
        "X-XSS-Protection": f'add_header X-XSS-Protection "1; mode=block";',
    }
    base = fixes.get(header, f"Set the {header} header in your server configuration.")
    if "add_header" in base:
        base += f"\n  Add to nginx config (or equivalent for Apache/IIS):\n  {base}"
    return base


def _fallback_report(analysis: dict, has_root_access: bool) -> str:
    lines = [
        "## Executive Summary",
        f"This website achieved a security grade of **{analysis.get('grade', 'N/A')}**.",
        "",
        "## Issues Found",
    ]
    for i in (analysis.get("issues") or []):
        lines.append(f"- **{i['severity'].upper()}**: {i['message']}")
        lines.append(f"  {i.get('description', '')}")

    lines.extend(["", "## Security Headers"])
    for hdr, info in (analysis.get("security_headers") or {}).items():
        status = "✅" if info["present"] else "❌"
        lines.append(f"- {status} **{hdr}**: {info['description']}")

    if not has_root_access:
        lines.extend(["", "## Security Advisory (No Root Access)",
                       "Since you don't have root/server-level access, here are practical mitigations:",
                       "- Use your hosting control panel to add security headers",
                       "- Install a security plugin (e.g., Wordfence for WordPress)",
                       "- Enable HTTPS through your hosting provider",
                       "- Use Cloudflare for additional security headers",
                       "- Restrict file permissions via FTP/SFTP"])

    lines.extend(["", "## Recommendations"])
    for i in (analysis.get("issues") or [])[:5]:
        lines.append(f"- {i.get('fix', 'Review and address this issue')}")

    lines.append(f"\n## Overall Assessment: {analysis.get('grade', 'N/A')}")
    return "\n".join(lines)
