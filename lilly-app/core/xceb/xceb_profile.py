#!/usr/bin/env python3
"""
XCEB profile — dossier renderer.

Produces profile.html + profile.txt for a case, always showing:
  * the discovered identity (or 'Unknown #…'), with an honest % confidence
    bar built ONLY from observed evidence,
  * every source as a clickable link (the actual URL it was seen on),
  * a clear "UNVERIFIED LEAD" marker on anything that has not been confirmed,
  * warnings when the pipeline could not resolve identity, and the option to
    re-run/retry later.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path

from xceb_engines import site_name
from xceb_graph import svg_graph


def _esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def confidence_bar(conf: float) -> str:
    pct = max(0.0, min(1.0, float(conf or 0)))
    color = "#53d3a8" if pct >= 0.55 else ("#ffd166" if pct >= 0.3 else "#ff8fab")
    return (
        f'<div class="bar"><div style="width:{pct * 100:.0f}%;background:{color}"></div></div>'
        f'<span class="pct" style="color:{color}">{pct * 100:.0f}%</span>'
    )


def _cards(items: list[dict], fields: tuple) -> str:
    out = []
    for it in items:
        url = it.get("url") or (it.get("urls") or [None])[0]
        chunk = ""
        if it.get("snippet"):
            chunk = f'<div class="snip">{_esc(it["snippet"][:260])}</div>'
        if it.get("title"):
            chunk = f'<div class="snip"><b>{_esc(it["title"][:120])}</b></div>' + chunk
        if it.get("value"):
            chunk = f'<div class="lead">{_esc(it["value"])}</div>' + chunk
        src = ""
        for su in it.get("urls") or ([url] if url else []):
            if su:
                src += f'<a class="src" href="{_esc(su)}" target="_blank" rel="noopener">{_esc(site_name(su))}</a> '
        if url and not it.get("urls"):
            src += f'<a class="src" href="{_esc(url)}" target="_blank" rel="noopener">{_esc(site_name(url))}</a>'
        conf = it.get("confidence") or it.get("confirmed") or 0
        out.append(
            f'<div class="card"><div class="cardrow">'
            f'<div class="cardmain">{chunk}</div>'
            f'<div class="cardmeta"><span class="tag">{_esc(it.get("engine", it.get("type", "")))}</span>'
            f'<span class="conf">{conf * 100:.0f}%</span></div></div>{src}</div>'
        )
    return "".join(out)


def render_profile(case: dict) -> tuple[str, str]:
    """Return (profile_html, profile_txt)."""
    name = case.get("best_name") or (
        case.get("slug_name") or f"Unknown #{case['case_id'][-6:]}"
    )
    f = case.get("findings", {})
    status = case.get("status", "new")
    created = case.get("created", "")
    updated = case.get("updated", "")
    conf = case.get("confidence") or 0.0
    warnings = case.get("warnings", [])

    reverse = f.get("reverse", [])
    accounts = f.get("accounts", [])
    social = [a for a in accounts if a.get("type") == "social"]
    dating = [a for a in accounts if a.get("type") == "dating"]
    fan = [a for a in accounts if a.get("type") == "fan"]
    emails, phones, addresses, records = (
        f.get("emails", []),
        f.get("phones", []),
        f.get("addresses", []),
        f.get("records", []),
    )
    geo_hint = case.get("geo_hint", "")
    llm = case.get("llm", {}) or {}

    nx = "🟢" if conf >= 0.55 else ("🟡" if conf >= 0.3 else "⚪")
    banner = (
        "RESOLVED — highest-confidence identity, confirm before acting on records"
        if conf >= 0.55
        else (
            "LEAD — plausible name, needs confirmation (more sources / retry tonight)"
            if conf >= 0.3
            else "UNRESOLVED — no confident identity yet; will auto-retry nightly"
        )
    )

    html_txt = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XCEB · {_esc(name)}</title>
<style>
:root{{--bg:#0d0c18;--panel:#141326;--line:#2b2744;--txt:#dfe0f5;--dim:#8b88b0;--acc:#a784f2}}
*{{box-sizing:border-box}}body{{background:var(--bg);color:var(--txt);font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;margin:0;padding:28px}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 2px}}h2{{font-size:14px;letter-spacing:.08em;text-transform:uppercase;color:var(--acc);margin:34px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}}
a{{color:#7fb7ff;text-decoration:none}}a:hover{{text-decoration:underline}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}}
.meta{{color:var(--dim);font-size:12px;margin-top:8px}}
.bar{{width:220px;height:8px;background:#241f3f;border-radius:99px;overflow:hidden;display:inline-block;vertical-align:middle}}
.bar>div{{height:100%}}.pct{{margin-left:8px;font-size:20px;font-weight:700}}
.banner{{margin:16px 0;padding:10px 14px;border-radius:10px;border:1px solid var(--line);font-size:13px}}
.ok{{border-color:#2e5a4b;color:#8fe8c8;background:#10241d}}
.warn{{border-color:#5a4f2e;color:#f4df8f;background:#241f10}}
.photos{{display:flex;gap:14px;margin:10px 0}}
.photos img{{width:150px;height:150px;object-fit:cover;border-radius:12px;border:1px solid var(--line)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px}}
.card{{background:#0f0e1d;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:12.5px}}
.cardrow{{display:flex;justify-content:space-between;gap:8px}}
.cardmain{{flex:1;min-width:0}}.snip{{color:var(--dim);overflow-wrap:anywhere;margin:4px 0}}
.lead{{color:#fff;font-weight:600;overflow-wrap:anywhere}}
.cardmeta{{text-align:right;white-space:nowrap}}.tag{{color:var(--acc);font-size:11px}}
.conf{{font-size:11px;color:var(--dim);display:block;margin-top:2px}}
.src{{font-size:11px;color:#6ec2ff;display:inline-block;margin:2px 6px 0 0;border:1px solid #23375a;border-radius:99px;padding:1px 8px}}
.empty{{color:var(--dim);font-size:12.5px}}
.warnbox{{color:#ffb4a2;font-size:12.5px}}.gridsvg{{width:100%;height:auto;border:1px solid var(--line);border-radius:12px;margin-top:8px}}
.foot{{color:var(--dim);font-size:11.5px;margin:40px 0 20px;border-top:1px solid var(--line);padding-top:14px}}
</style></head><body><div class="wrap">

<div class="panel">
<h1>{"🧬" if conf >= 0.55 else "🔎"} {_esc(name)}</h1>
<div class="meta">case {_esc(case["case_id"])} · status <b>{_esc(status)}</b> · created {_esc(created)} · updated {_esc(updated)}</div>
<div style="margin-top:14px">{confidence_bar(conf)}</div>
<div class="banner {"ok" if conf >= 0.55 else "warn"}">{_esc(banner)}</div>
"""
    # photos
    photo_html = ""
    pdf = Path(case["dir"])
    if (pdf / "face.jpg").exists():
        photo_html += f'<img src="face.jpg" alt="face crop">'
    if (pdf / "original.jpg").exists():
        photo_html += f'<img src="original.jpg" alt="original frame">'
    if photo_html:
        html_txt += f'<div class="panel"><h2>Source images</h2><div class="photos">{photo_html}</div></div>'

    # geo + category
    html_txt += f"""<div class="panel" style="margin-top:16px">
<div style="display:flex;gap:26px;flex-wrap:wrap;font-size:13px">
<span>🎯 <b>Geo focus</b>: {_esc(geo_hint or "worldwide")}</span>
<span>🗂 <b>Category filter</b>: {_esc(case.get("category") or "all")}</span>
<span>ℹ️ <b>Source</b>: {_esc(case.get("source") or "")}</span>
</div></div>"""

    if llm:
        html_txt += f"""<div class="panel" style="margin-top:16px"><h2>LLM analysis</h2>
<div class="cards"><div class="card"><div class="cardmain"><b>{_esc(llm.get("name") or name)}</b>
<div class="snip">{_esc(llm.get("reason") or "")}</div>
<div class="snip">{", ".join(_esc(a) for a in (llm.get("aliases") or [])[:6]) or ""}</div></div></div></div></div>"""

    # reverse search hits
    html_txt += f"""<h2>Reverse image search ({len(reverse)} hits)</h2>
<div class="cards">{_cards(reverse, ()) if reverse else '<div class="empty">No reverse hits — engines may be rate-limited or the face is not indexed publicly.</div>'}</div>"""

    # accounts
    def _accounts(name2: str):
        items = [a for a in accounts if a.get("type") == name2]
        return (
            "".join(
                f'<div class="card"><div class="cardmain"><b>{_esc(a["platform"])}</b>'
                f'<div class="snip"><a href="{_esc(a["url"])}" target="_blank" rel="noopener">{_esc(a["url"])}</a></div>'
                f'<div class="snip">{_esc(a.get("snippet") or a.get("title") or "")[:180]}</div></div>'
                f'<div class="cardmeta"><span class="tag">{_esc(a.get("type") or "")}</span>'
                f'<span class="conf">{float(a.get("confidence") or 0) * 100:.0f}%</span></div></div>'
                for a in items
            )
            if items
            else '<div class="empty">None found yet.</div>'
        )

    html_txt += f"""<h2>Social media ({len(social)})</h2><div class="cards">{_accounts("social")}</div>
<h2>Dating sites ({len(dating)})</h2><div class="cards">{_accounts("dating")}</div>
<h2>Fan platforms ({len(fan)})</h2><div class="cards">{_accounts("fan")}</div>"""

    # structured data (unverified leads)
    html_txt += f"""<h2>Contact & residence — ⚠️ UNVERIFIED LEADS</h2>
<p class="empty">Extracted from public search snippets only. Nothing here is confirmed fact; verify each item independently before acting.</p>
<h2>Email addresses ({len(emails)})</h2><div class="cards">{_cards(emails, ()) if emails else '<div class="empty">None found in public snippets.</div>'}</div>
<h2>Phone numbers ({len(phones)})</h2><div class="cards">{_cards(phones, ()) if phones else '<div class="empty">None found in public snippets.</div>'}</div>
<h2>Addresses ({len(addresses)})</h2><div class="cards">{_cards(addresses, ()) if addresses else '<div class="empty">None found in public snippets.</div>'}</div>"""

    # records
    rec_cards = ""
    if records:
        for r in records[:24]:
            rec_cards += (
                f'<div class="card"><div class="cardmain"><b>{_esc(r.get("type", "record").replace("_", " ").title())}</b>'
                f'<div class="snip">{_esc(r.get("snippet", "")[:220])}</div></div>'
                f'<div class="cardmeta"><span class="tag">lead</span><span class="conf">'
                f"{float(r.get('confidence') or 0) * 100:.0f}%</span></div>"
                f"<div>"
                + "".join(
                    f'<a class="src" href="{_esc(s)}" target="_blank" rel="noopener">{_esc(site_name(s))}</a>'
                    for s in (r.get("sources") or [])[:4]
                )
                + "</div></div>"
            )
    html_txt += f"""<h2>Public records ({len(records)}) — ⚠️ LEADS ONLY</h2>
<div class="cards">{rec_cards if rec_cards else '<div class="empty">No court / warrant / marriage / property leads surfaced from free sources.</div>'}</div>"""

    # graph
    try:
        graph = json.loads((Path(case["dir"]) / "graph.json").read_text())
        svg = svg_graph(graph)
    except Exception:
        svg = "<div class='empty'>graph.json missing</div>"
    html_txt += f"""<h2>Connection map</h2><div class="panel">{svg}</div>"""

    # warnings + steps tail
    warn_txt = "".join(f'<div class="warnbox">⚠️ {_esc(w)}</div>' for w in warnings)
    steps = case.get("steps", [])[-12:]
    step_txt = "".join(
        f'<div class="snip">[{_esc(s.get("t") or "")}] {_esc(s.get("msg") or "")}</div>'
        for s in steps
    )
    html_txt += f"""<h2>Warnings</h2><div class="panel">{warn_txt or '<div class="empty">none</div>'}</div>
<h2>Recent activity</h2><div class="panel">{step_txt or '<div class="empty">no steps recorded</div>'}</div>
<div class="foot">XCEB · Face/OSINT Evidence Broker · {_esc(updated)} · confidence reflects only observed sources, never fabricated data</div>
</div></body></html>"""

    # ── plain text ────────────────────────────────────────────────────────
    txt = []
    txt.append(f"=== XCEB PROFILE === {name} ({conf * 100:.0f}% confidence) ===")
    txt.append(f"case_id: {case['case_id']}  status: {status}  created: {created}")
    txt.append(
        f"geo_focus: {geo_hint or 'worldwide'}  category: {case.get('category') or 'all'}"
    )
    txt.append(banner.upper() + "\n")
    txt.append("REVERSE IMAGE SEARCH:")
    for h in reverse[:12]:
        txt.append(
            f"  · {h.get('name') or '?'}  [{h.get('engine')}]  {h.get('href') or ''}  {h.get('site') or ''}"
        )
    if not reverse:
        txt.append("  (none)")
    txt.append("\nSOCIAL MEDIA:")
    for a in social:
        txt.append(f"  · {a['platform']}: {a['url']}")
    txt.append("\nDATING SITES:")
    for a in dating:
        txt.append(f"  · {a['platform']}: {a['url']}")
    txt.append("\nFAN PLATFORMS:")
    for a in fan:
        txt.append(f"  · {a['platform']}: {a['url']}")
    txt.append("\nEMAILS [unverified leads]:")
    for e in emails:
        txt.append(f"  · {e['value']}  (seen: {', '.join(e.get('urls', [])[:3])})")
    txt.append("\nPHONES [unverified leads]:")
    for p in phones:
        txt.append(f"  · {p['value']}  (seen: {', '.join(p.get('urls', [])[:3])})")
    txt.append("\nADDRESSES [unverified leads]:")
    for a in addresses:
        txt.append(f"  · {a['value']}  (seen: {', '.join(a.get('urls', [])[:3])})")
    txt.append("\nPUBLIC RECORDS [LEADS ONLY]:")
    for r in records[:20]:
        txt.append(
            f"  · [{r.get('type')}] ({r.get('source_site') or ''}) {r.get('snippet', '')[:160]}"
        )
        for s in (r.get("sources") or [])[:3]:
            txt.append(f"      src: {s}")
    txt.append("\nWARNINGS:")
    for w in warnings:
        txt.append(f"  · {w}")
    for s in steps[-8:]:
        txt.append(f"[{s.get('t')}] {s.get('msg')}")
    txt.append(
        "\nNOTE: profiles are compiled from live public sources; leads must be verified independently."
    )
    txt_txt = "\n".join(txt)
    return html_txt, txt_txt


def write_profile_files(case: dict) -> list[str]:
    h, t = render_profile(case)
    cdir = Path(case["dir"])
    (cdir / "profile.html").write_text(h)
    (cdir / "profile.txt").write_text(t)
    try:
        from xceb_graph import build_graph, maltego_csv, svg_graph

        g = build_graph(case)
        (cdir / "graph.json").write_text(json.dumps(g, indent=2))
        (cdir / "maltego_export.csv").write_text(maltego_csv(case, g))
        # Standalone SVG served by GET /xceb/cases/{cid}/graph.svg. Also
        # embedded inline in profile.html; this file makes the URL real.
        (cdir / "graph.svg").write_text(svg_graph(g))
    except Exception as e:
        case.setdefault("warnings", []).append(f"graph export failed: {e}")
    return [
        "profile.html",
        "profile.txt",
        "graph.json",
        "maltego_export.csv",
        "graph.svg",
    ]
