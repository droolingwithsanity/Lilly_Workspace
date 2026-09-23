#!/usr/bin/env python3
"""
XCEB graph — connection mapping + Maltego CE export + inline SVG.
Builds an entity graph from a case's findings:

    nodes: person, account (per platform), email, phone, address, source(page)
    edges: typed (is_account_of | claimed_email | claimed_phone | lives_at |
           appears_on | alias_of)

Exports:
    graph.json          full entity graph (for the web UI / APIs)
    maltego_export.csv  Maltego Community "Import → CSV" compatible export
    graph.svg           simple inline visualisation used in the profile page
"""

from __future__ import annotations

import csv
import html
import io
import json
import logging

logger = logging.getLogger("xceb.graph")


def build_graph(case: dict) -> dict:
    f = case.get("findings", {})
    name = (
        case.get("best_name")
        or case.get("slug_name")
        or f"Person {case['case_id'][-6:]}"
    )
    nodes: list[dict] = []
    edges: list[dict] = []
    seen = set()

    def add_node(nid: str, label: str, kind: str, **kw):
        if nid in seen:
            return
        seen.add(nid)
        nodes.append({"id": nid, "label": label, "kind": kind, **kw})

    def add_edge(src: str, dst: str, rel: str, **kw):
        edges.append({"source": src, "target": dst, "relation": rel, **kw})

    pid = "person:" + name.lower()
    add_node(pid, name, "person", resolved=bool(case.get("best_name")))

    for acc in f.get("accounts", []):
        aid = "acct:" + acc.get("url", "")
        handle = acc.get("username") or acc.get("url")
        add_node(
            aid,
            f"{acc.get('platform', '?')} / {handle}",
            "account",
            url=acc.get("url"),
            category=acc.get("type"),
            confidence=acc.get("confidence"),
        )
        add_edge(pid, aid, "is_account_of")

    for e in f.get("emails", []):
        eid = "email:" + e.get("value", "")
        add_node(eid, e.get("value", ""), "email", seen_on=e.get("urls", []))
        add_edge(pid, eid, "claimed_email")

    for p in f.get("phones", []):
        xid = "phone:" + p.get("value", "")
        add_node(xid, p.get("value", ""), "phone", seen_on=p.get("urls", []))
        add_edge(pid, xid, "claimed_phone")

    for a in f.get("addresses", []):
        aid2 = "addr:" + a.get("value", "")[:40].lower()
        add_node(aid2, a.get("value", ""), "address", seen_on=a.get("urls", []))
        add_edge(pid, aid2, "lives_at")

    for r in f.get("records", []):
        rid = "rec:" + r.get("type", "") + ":" + r.get("snippet", "")[:40].lower()
        add_node(
            rid,
            r.get("type", "record").replace("_", " ").title(),
            "record",
            source_site=r.get("source_site"),
            snippet=r.get("snippet", ""),
            urls=r.get("sources", []),
        )
        add_edge(pid, rid, "appears_in", type=r.get("type"))

    for h in f.get("reverse", [])[:6]:
        if not h.get("href"):
            continue
        sid = "src:" + h["href"]
        add_node(
            sid,
            h.get("site") or h["href"],
            "source",
            url=h["href"],
            title=h.get("title", ""),
        )
        add_edge(pid, sid, "appears_on")

    for al in f.get("aliases", []):
        nid2 = "alias:" + str(al).lower()
        add_node(nid2, str(al), "person", alias=True)
        add_edge(pid, nid2, "alias_of")

    return {
        "person": name,
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "case_id": case["case_id"],
            "slug": case.get("slug"),
            "confidence": case.get("confidence"),
        },
    }


def maltego_csv(case: dict, graph: dict | None = None) -> str:
    """CSV in Maltego Community import format (Entity, Property, Value)."""
    g = graph or build_graph(case)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Entity", "Property", "Value"])
    for n in g["nodes"]:
        etype = {
            "person": "maltego.Person",
            "account": "maltego.Person",
            "email": "maltego.EmailAddress",
            "phone": "maltego.PhoneNumber",
            "address": "maltego.Location",
            "record": "maltego.Document",
            "source": "maltego.URL",
        }.get(n.get("kind"), "maltego.Phrase")
        w.writerow([etype, "value", n.get("label") or n.get("id")])
        if n.get("url"):
            w.writerow([etype, "url", n["url"]])
        if n.get("source_site"):
            w.writerow([etype, "source", n["source_site"]])
        if n.get("snippet"):
            w.writerow([etype, "notes", n["snippet"][:240]])
    for e in g["edges"]:
        w.writerow(["maltego.Relationship", "source", e["source"]])
        w.writerow(["maltego.Relationship", "target", e["target"]])
        w.writerow(["maltego.Relationship", "type", e["relation"]])
    return buf.getvalue()


def svg_graph(graph: dict, width: int = 900, height: int = 620) -> str:
    """Small dependency-free SVG force-ish layout (circular person-centric)."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return "<svg/>"
    cx, cy = width // 2, height // 2
    kind_order = {
        "person": 0,
        "source": 1,
        "account": 2,
        "email": 3,
        "phone": 4,
        "address": 5,
        "record": 6,
        "alias": 7,
    }
    sorted_nodes = sorted(nodes, key=lambda n: kind_order.get(n.get("kind"), 8))
    person = next(
        (n for n in sorted_nodes if n.get("kind") == "person"), sorted_nodes[0]
    )
    others = [n for n in sorted_nodes if n != person]
    pos: dict[str, tuple[int, int]] = {person["id"]: (cx, cy)}

    rings = [
        (170, [n for n in others if n.get("kind") in ("source", "account")]),
        (260, [n for n in others if n.get("kind") in ("email", "phone", "address")]),
        (330, [n for n in others if n.get("kind") in ("record", "alias")]),
    ]
    leftover = [n for n in others if not any(n in ring for _, ring in rings)]
    for i, n in enumerate(leftover):
        ang = i * (360 / max(1, len(leftover)))
        pos[n["id"]] = (
            cx + int(120 * __import__("math").cos(__import__("math").radians(ang))),
            cy + int(120 * __import__("math").sin(__import__("math").radians(ang))),
        )
    for radius, group in rings:
        for i, n in enumerate(group):
            ang = i * (360 / max(1, len(group)))
            import math

            pos[n["id"]] = (
                cx + int(radius * math.cos(math.radians(ang))),
                cy + int(radius * math.sin(math.radians(ang))),
            )

    color = {
        "person": "#a784f2",
        "source": "#6ec2ff",
        "account": "#53d3a8",
        "email": "#ffd166",
        "phone": "#ff8fab",
        "address": "#c19f5f",
        "record": "#f4a261",
        "alias": "#8ab4f8",
    }
    svg = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    svg.append(
        f'<rect width="{width}" height="{height}" rx="18" fill="#141326" stroke="#2b2744"/>'
    )
    edge_seen = set()
    for e in edges:
        if e["source"] not in pos or e["target"] not in pos:
            continue
        k = (e["source"], e["target"])
        if k in edge_seen:
            continue
        edge_seen.add(k)
        x1, y1 = pos[e["source"]]
        x2, y2 = pos[e["target"]]
        svg.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#7a6fd0" '
            f'stroke-opacity="0.45" stroke-width="1"/>'
        )
    for n in nodes:
        if n["id"] not in pos:
            continue
        x, y = pos[n["id"]]
        fill = color.get(n.get("kind"), "#9aa3b2")
        svg.append(
            f'<circle cx="{x}" cy="{y}" r="10" fill="{fill}" stroke="#141326" stroke-width="2"/>'
        )
        label = html.escape((n.get("label") or "")[:18])
        svg.append(
            f'<text x="{x}" y="{y - 14}" fill="#dfe0f5" font-size="9" text-anchor="middle" '
            f'font-family="monospace">{label}</text>'
        )
    svg.append("</svg>")
    return "\n".join(svg)
