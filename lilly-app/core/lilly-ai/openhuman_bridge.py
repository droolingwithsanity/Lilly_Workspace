"""
openhuman_bridge.py — HTTP bridge for the OpenHuman skill registry.

This service exposes the OpenHuman `skill_registry_*` RPC methods as plain HTTP
endpoints so that Lilly AI (Python) and the OpenLive bridge (Node.js) can
browse, search, and install skills from the OpenHuman community catalog
without needing the Rust core binary running.

The catalog is fetched from the Hermes skill registry (the same URL the Rust
core uses: https://hermes-agent.nousresearch.com/docs/api/skills.json) and
cached on disk with a TTL, mirroring the Rust store layer's behaviour.

Endpoints:
  GET  /health                    — service health
  GET  /v1/catalog               — browse full catalog (cached, force_refresh=1 supported)
  GET  /v1/catalog/search?q=...  — search by keyword
  GET  /v1/catalog/sources       — list source names
  GET  /v1/catalog/categories    — list category names
  GET  /v1/skills                — list installed skills (from SKILLS_FILE)
  POST /v1/skills/install        — install a skill from the catalog by entry_id
  POST /v1/skills/uninstall      — uninstall an installed skill by name
  GET  /v1/skills/:id/describe   — describe a single installed skill

Configuration via environment variables:
  OPENHUMAN_CATALOG_URL   — override the catalog URL (default: Hermes)
  OPENHUMAN_CACHE_DIR     — cache directory (default: ~/.cache/openhuman_bridge)
  OPENHUMAN_CACHE_TTL     — cache TTL in seconds (default: 86400 = 24h)
  OPENHUMAN_SKILLS_FILE   — path to the skills JSON file to read/write
  OPENHUMAN_DATA_DIR      — directory for installed skill packages
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ── Configuration ─────────────────────────────────────────────────────────────

CATALOG_URL = os.environ.get(
    "OPENHUMAN_CATALOG_URL",
    "https://nousresearch.github.io/hermes-agent/docs/api/skills.json",
)
CACHE_DIR = Path(
    os.environ.get(
        "OPENHUMAN_CACHE_DIR",
        str(Path.home() / ".cache" / "openhuman_bridge"),
    )
)
CACHE_TTL = int(os.environ.get("OPENHUMAN_CACHE_TTL", "86400"))
SKILLS_FILE = Path(
    os.environ.get(
        "OPENHUMAN_SKILLS_FILE",
        str(Path.home() / ".cache" / "openhuman_bridge" / "skills.json"),
    )
)
DATA_DIR = Path(
    os.environ.get(
        "OPENHUMAN_DATA_DIR",
        str(CACHE_DIR / "skills"),
    )
)

FETCH_TIMEOUT = 180  # seconds — match the Rust core's FETCH_TIMEOUT_SECS

logger = logging.getLogger("openhuman_bridge")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="OpenHuman Skill Bridge", version="0.1.0")


# ── Data models (mirror OpenHuman's Rust types) ───────────────────────────────


class CatalogEntry(BaseModel):
    """One entry in the OpenHuman skill catalog."""

    id: str
    name: str
    description: str
    source: str
    category: str
    author: str | None = None
    version: str | None = None
    tags: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    download_url: str
    docs_path: str | None = None
    commands: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    license: str | None = None
    source_url: str | None = None


class InstalledSkill(BaseModel):
    """A skill installed into Lilly's lilly_skills.json format."""

    id: str
    name: str
    description: str
    package: str | None = None
    uri_template: str | None = None
    intent_action: str | None = None
    aliases: list[str] = Field(default_factory=list)
    canned_reply: str | None = None
    action_type: str = "intent_launch"
    source: str = "openhuman"
    download_url: str | None = None
    tags: list[str] = Field(default_factory=list)


class InstallResponse(BaseModel):
    url: str
    stdout: str
    stderr: str
    new_skills: list[str] = Field(default_factory=list)


class UninstallResponse(BaseModel):
    name: str
    removed_path: str
    scope: str = "user"


# ── Cache layer ───────────────────────────────────────────────────────────────


def cache_path() -> Path:
    return CACHE_DIR / "catalog.json"


def load_cached_catalog() -> list[dict[str, Any]] | None:
    """Load the cached catalog if it exists and is fresh."""
    path = cache_path()
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
        if time.time() - mtime > CACHE_TTL:
            logger.info("catalog cache expired (age=%ds)", int(time.time() - mtime))
            return None
        data = json.loads(path.read_text())
        logger.info("loaded cached catalog: %d entries", len(data))
        return data
    except Exception as e:
        logger.warning("failed to load cached catalog: %s", e)
        return None


def save_cached_catalog(entries: list[dict[str, Any]]) -> None:
    """Persist the catalog to disk cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path()
    path.write_text(json.dumps(entries, indent=2))
    logger.info("saved catalog cache: %d entries", len(entries))


async def fetch_catalog_fresh() -> list[dict[str, Any]]:
    """Fetch the catalog from the remote URL."""
    logger.info("fetching catalog from %s", CATALOG_URL)
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT, follow_redirects=True
    ) as client:
        resp = await client.get(CATALOG_URL, headers={"User-Agent": "openhuman-bridge"})
        resp.raise_for_status()
        body = resp.text

    # The catalog can be either a bare JSON array or wrapped in {"skills": [...]} / {"data": [...]}
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"invalid catalog JSON: {e}")

    items: list[dict[str, Any]]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("skills") or raw.get("data") or raw.get("entries") or []
        if not isinstance(items, list):
            items = []
    else:
        items = []

    logger.info("fetched %d raw catalog entries", len(items))
    return items


async def get_catalog(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Get the catalog, using cache unless force_refresh."""
    if not force_refresh:
        cached = load_cached_catalog()
        if cached is not None:
            return cached
    entries = await fetch_catalog_fresh()
    save_cached_catalog(entries)
    return entries


def _download_url_from_docs_path(docs_path: str) -> str:
    """Derive a raw GitHub download URL from a Hermes docs_path.

    Matches the Rust core's download_url_from_docs_path:
      bundled/{category}/{category}-{slug}  →  skills/{category}/{slug}/SKILL.md
      optional/{category}/{category}-{slug} →  optional-skills/{category}/{slug}/SKILL.md
    """
    parts = docs_path.split("/")
    if len(parts) != 3:
        return ""
    root = {"bundled": "skills", "optional": "optional-skills"}.get(parts[0])
    if not root:
        return ""
    category = parts[1]
    prefixed_slug = parts[2]
    skill = prefixed_slug
    prefix = f"{category}-"
    if skill.startswith(prefix):
        skill = skill[len(prefix) :]
    return f"https://raw.githubusercontent.com/NousResearch/hermes-agent/main/{root}/{category}/{skill}/SKILL.md"


def _download_url_from_source_url(source_url: str) -> str:
    """Rewrite a GitHub sourceUrl (blob or tree view) to a raw SKILL.md URL.

    Matches the Rust core's download_url_from_source_url.
    Returns "" for non-GitHub hosts (portal-only community skills).
    """
    rest = source_url
    for prefix in ("https://github.com/", "http://github.com/"):
        if rest.startswith(prefix):
            rest = rest[len(prefix) :]
            break
    else:
        return ""

    # {owner}/{repo}/{blob|tree}/{branch}/{path...}
    parts = rest.split("/", 4)
    if len(parts) < 5:
        return ""
    owner, repo, kind, branch, path = parts
    if not owner or not repo or not branch or not path:
        return ""

    path = path.rstrip("/")
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    if kind == "blob":
        if raw.endswith("/SKILL.md") or raw.endswith(".md"):
            return raw
        return f"{raw}/SKILL.md"
    elif kind == "tree":
        return f"{raw}/SKILL.md"
    return ""


def derive_download_url(
    name: str,
    docs_path: str | None,
    source_url: str | None,
) -> str:
    """Derive a download_url from docs_path or source_url, matching the Rust core.

    Priority:
    1. OPENHUMAN_DOWNLOAD_BASE_URL env var (if set) → {base}/{name}/SKILL.md
    2. docs_path → raw GitHub URL from NousResearch/hermes-agent
    3. source_url → rewrite GitHub blob/tree to raw SKILL.md
    4. Empty string (portal-only, not downloadable)
    """
    base = os.environ.get("OPENHUMAN_DOWNLOAD_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/{name}/SKILL.md"
    if docs_path:
        url = _download_url_from_docs_path(docs_path)
        if url:
            return url
    if source_url:
        url = _download_url_from_source_url(source_url)
        if url:
            return url
    return ""


def parse_catalog_entry(item: dict[str, Any]) -> CatalogEntry:
    """Parse a raw catalog JSON entry into a CatalogEntry, matching the Rust parse_hermes_entry logic."""
    # Normalize `author` — the catalog may have a string, a list, or missing.
    raw_author = item.get("author")
    if isinstance(raw_author, list):
        author_str = ", ".join(str(a) for a in raw_author if a)
    elif raw_author is not None:
        author_str = str(raw_author)
    else:
        author_str = None

    # Normalize `tags` — could be a comma-separated string or list
    raw_tags = item.get("tags") or []
    if isinstance(raw_tags, str):
        tags_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
    elif isinstance(raw_tags, list):
        tags_list = [str(t) for t in raw_tags if t]
    else:
        tags_list = []

    # Normalize `platforms`
    raw_platforms = item.get("platforms") or []
    if isinstance(raw_platforms, str):
        platforms_list = [p.strip() for p in raw_platforms.split(",") if p.strip()]
    elif isinstance(raw_platforms, list):
        platforms_list = [str(p) for p in raw_platforms if p]
    else:
        platforms_list = []

    name = item.get("name") or ""
    docs_path = item.get("docs_path") or item.get("docsPath") or ""
    source_url = item.get("source_url") or item.get("sourceUrl") or item.get("repo")

    # Derive download_url exactly like the Rust core does
    download_url = (
        item.get("download_url") or item.get("downloadUrl") or item.get("url") or ""
    )
    if not download_url:
        download_url = derive_download_url(name, docs_path or None, source_url or None)

    return CatalogEntry(
        id=item.get("id") or item.get("slug") or name,
        name=name,
        description=item.get("description") or item.get("summary") or "",
        source=item.get("source") or "hermes",
        category=item.get("category") or "",
        author=author_str,
        version=item.get("version") or item.get("manifest_version"),
        tags=tags_list,
        platforms=platforms_list,
        download_url=download_url,
        docs_path=docs_path or None,
        commands=item.get("commands") or [],
        env_vars=item.get("env_vars") or item.get("envVars") or [],
        license=item.get("license"),
        source_url=source_url or None,
    )


# ── Skill file loading/saving ─────────────────────────────────────────────────


def load_skills_file() -> dict[str, Any]:
    """Load the skills JSON file (lilly_skills.json or whatever OPENHUMAN_SKILLS_FILE points to)."""
    if not SKILLS_FILE or not SKILLS_FILE.exists():
        return {}
    try:
        raw = json.loads(SKILLS_FILE.read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception as e:
        logger.warning("failed to load skills file %s: %s", SKILLS_FILE, e)
        return {}


def save_skills_file(skills: dict[str, Any]) -> None:
    """Write the skills dict back to the JSON file."""
    if not SKILLS_FILE:
        return
    SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SKILLS_FILE.write_text(json.dumps(skills, indent=2))


def normalize_key(key: str) -> str:
    """Normalize a skill key the same way lilly_ai.py does."""
    import unicodedata

    # Collapse to lowercase and strip accents
    nfkd = unicodedata.normalize("NFKD", key)
    cleaned = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", "_", cleaned.lower()).strip("_")


# ── Skill installation ────────────────────────────────────────────────────────


async def install_skill_from_url(
    url: str, target_dir: Path, timeout_secs: int = 60
) -> dict[str, Any]:
    """Fetch a SKILL.md file from a URL and write it into the target directory.

    Mirrors the Rust core's `install_workflow_from_url` behaviour:
    - GitHub blob URLs are rewritten to raw.githubusercontent.com
    - The file is validated and saved under target_dir/<slug>/
    """
    # Rewrite GitHub blob URLs to raw
    download_url = url
    if "github.com" in url and "/blob/" in url:
        download_url = url.replace("github.com", "raw.githubusercontent.com").replace(
            "/blob/", "/"
        )

    # Validate URL scheme
    parsed = urlparse(download_url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400, detail=f"URL must be http(s): {download_url}"
        )

    # SSRF guard — same as Lilly's intent: block loopback/private
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise HTTPException(status_code=400, detail="loopback URLs are not allowed")

    logger.info("installing skill from %s -> %s", download_url, target_dir)
    async with httpx.AsyncClient(timeout=timeout_secs) as client:
        resp = await client.get(
            download_url, headers={"User-Agent": "openhuman-bridge"}
        )
        resp.raise_for_status()
        content = resp.text

    # Parse frontmatter to get the slug
    slug = parse_skill_slug(content, url)
    skill_dir = target_dir / slug
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(content)

    logger.info("installed skill '%s' at %s", slug, skill_path)

    return {
        "url": download_url,
        "stdout": f"Installed skill '{slug}' to {skill_path}",
        "stderr": "",
        "new_skills": [slug],
        "path": str(skill_path),
    }


def parse_skill_slug(content: str, url: str) -> str:
    """Extract a slug from the SKILL.md frontmatter, falling back to the URL filename."""
    # Try YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 2:
            fm = parts[1]
            # Look for name: field
            name_match = re.search(
                r"^name:\s*[\"'']?([^\"'\n]+)[\"'']?$", fm, re.MULTILINE
            )
            if name_match:
                raw = name_match.group(1).strip()
                return normalize_key(raw)
            # Try slug
            slug_match = re.search(
                r"^slug:\s*[\"'']?([^\"'\n]+)[\"'']?$", fm, re.MULTILINE
            )
            if slug_match:
                return slug_match.group(1).strip()

    # Fall back to URL filename
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    filename = path.rsplit("/", 1)[-1]
    if filename.endswith(".md"):
        filename = filename[:-3]
    elif filename.endswith(".markdown"):
        filename = filename[:-7]
    return normalize_key(filename) if filename else "unnamed-skill"


def skill_to_lilly_format(catalog_entry: CatalogEntry) -> dict[str, Any]:
    """Convert a CatalogEntry to Lilly's lilly_skills.json format so each avatar
    can use the skill via its existing SKILLS lookup."""
    return {
        "action_type": "openhuman_skill",
        "type": "openhuman_skill",
        "label": catalog_entry.name,
        "source": catalog_entry.source,
        "download_url": catalog_entry.download_url,
        "intent_action": "openhuman.SKILL_EXECUTE",
        "uri_template": catalog_entry.download_url or "",
        "aliases": [
            catalog_entry.name.lower(),
            normalize_key(catalog_entry.name),
        ]
        + [t.lower() for t in catalog_entry.tags[:5]],
        "canned_reply": catalog_entry.description,
        "commands": catalog_entry.commands,
        "env_vars": catalog_entry.env_vars,
        "tags": catalog_entry.tags,
        "category": catalog_entry.category,
        "version": catalog_entry.version,
        "package": "",  # No Android package — these are agent skills
        # The skill_id is preserved so lilly_ai.py can look up the catalog
        # entry by id when executing. Keys are prefixed with 'openhuman_'
        # by the caller to avoid collisions with built-in Android skills.
        "skill_id": catalog_entry.id or catalog_entry.name,
    }


# ── HTTP Routes ───────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "openhuman-bridge",
        "catalog_url": CATALOG_URL,
        "cache_dir": str(CACHE_DIR),
        "skills_file": str(SKILLS_FILE),
        "data_dir": str(DATA_DIR),
    }


@app.get("/v1/catalog")
async def browse_catalog(
    force_refresh: bool = Query(
        False, description="Force a fresh fetch, bypassing cache"
    ),
    include_skills: bool = Query(
        False,
        description="Also include capability skills (where registry installs land)",
    ),
):
    """Browse the full OpenHuman skill catalog.

    Mirrors OpenHuman's `openhuman.skill_registry_browse` RPC.
    """
    raw_entries = await get_catalog(force_refresh=force_refresh)
    entries = [parse_catalog_entry(e) for e in raw_entries]
    return {"entries": [e.model_dump() for e in entries]}


@app.get("/v1/catalog/search")
async def search_catalog(
    q: str = Query(..., description="Search query"),
    source: str | None = Query(None, description="Filter by source"),
    category: str | None = Query(None, description="Filter by category"),
):
    """Search the OpenHuman skill catalog by keyword.

    Mirrors OpenHuman's `openhuman.skill_registry_search` RPC.
    """
    raw_entries = await get_catalog(force_refresh=False)
    query_lower = q.lower()
    results: list[dict[str, Any]] = []

    for entry in raw_entries:
        entry_obj = parse_catalog_entry(entry)
        # Search across name, description, tags, aliases
        searchable = " ".join(
            [
                entry_obj.name,
                entry_obj.description,
                " ".join(entry_obj.tags),
                entry_obj.category,
                entry_obj.source,
            ]
        ).lower()

        if query_lower in searchable:
            # Apply source/category filters
            if source and entry_obj.source.lower() != source.lower():
                continue
            if category and entry_obj.category.lower() != category.lower():
                continue
            results.append(entry_obj.model_dump())

    logger.info("search '%s' matched %d entries", q, len(results))
    return {"entries": results}


@app.get("/v1/catalog/sources")
async def list_sources():
    """List all distinct source names in the catalog."""
    raw_entries = await get_catalog(force_refresh=False)
    sources = sorted(
        set(
            parse_catalog_entry(e).source
            for e in raw_entries
            if parse_catalog_entry(e).source
        )
    )
    return {"sources": sources}


@app.get("/v1/catalog/categories")
async def list_categories():
    """List all distinct category names in the catalog."""
    raw_entries = await get_catalog(force_refresh=False)
    categories = sorted(
        set(
            parse_catalog_entry(e).category
            for e in raw_entries
            if parse_catalog_entry(e).category
        )
    )
    return {"categories": categories}


@app.get("/v1/skills")
async def list_installed_skills():
    """List skills already installed in the skills file (lilly_skills.json).

    Only returns skills with source='openhuman' to distinguish from built-in
    Android skills.
    """
    skills = load_skills_file()
    installed = [
        {"id": k, "name": v.get("label", k), **v}
        for k, v in skills.items()
        if isinstance(v, dict) and v.get("action_type") == "openhuman_skill"
    ]
    return {"skills": installed}


@app.post("/v1/skills/install")
async def install_skill(req: dict[str, Any]):
    """Install a skill from the catalog by entry_id.

    Mirrors OpenHuman's `openhuman.skill_registry_install` RPC.
    Fetches the SKILL.md from the catalog entry's download_url, writes it to
    the data directory, and registers it in the skills JSON file so Lilly avatars
    can use it immediately.
    """
    entry_id = req.get("entry_id") or req.get("id")
    if not entry_id:
        raise HTTPException(status_code=400, detail="entry_id is required")

    # Look up the entry in the catalog
    raw_entries = await get_catalog(force_refresh=False)
    entry = None
    for raw in raw_entries:
        parsed = parse_catalog_entry(raw)
        if parsed.id == entry_id:
            entry = parsed
            break

    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"entry '{entry_id}' not found in catalog. Run /v1/catalog first.",
        )

    if not entry.download_url or not entry.download_url.strip():
        where = f" View it at {entry.source_url}." if entry.source_url else ""
        raise HTTPException(
            status_code=422,
            detail=f"'{entry.name}' is hosted on {entry.source} and has no direct SKILL.md download.{where}",
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    outcome = await install_skill_from_url(
        entry.download_url, DATA_DIR, timeout_secs=60
    )

    # Register the skill in the skills file so Lilly's SKILLS dict picks it up.
    # Keys are prefixed with 'openhuman_' to avoid collisions with built-in
    # Android skills (e.g. a community skill named "email" won't override
    # Lilly's built-in email skill).
    skills = load_skills_file()
    lilly_skill = skill_to_lilly_format(entry)
    key = f"openhuman_{normalize_key(entry.id)}"
    skills[key] = lilly_skill
    # Also register under aliases so avatar skill matching works
    for alias in lilly_skill.get("aliases", []):
        norm = f"openhuman_{normalize_key(alias)}"
        if norm and norm != key:
            skills[norm] = lilly_skill
    save_skills_file(skills)

    return InstallResponse(**outcome)


@app.post("/v1/skills/uninstall")
async def uninstall_skill(req: dict[str, Any]):
    """Uninstall an installed skill by name.

    Mirrors OpenHuman's `openhuman.skill_registry_uninstall` RPC.
    """
    name = req.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    skills = load_skills_file()
    # Find the skill and its aliases — match on prefixed keys or label
    to_remove = [
        k
        for k, v in skills.items()
        if isinstance(v, dict)
        and (
            k == f"openhuman_{normalize_key(name)}"
            or k == normalize_key(name)
            or v.get("label", "").lower() == name.lower()
            or normalize_key(v.get("label", "")) == normalize_key(name)
        )
    ]

    if not to_remove:
        raise HTTPException(status_code=404, detail=f"skill '{name}' not found")

    removed_paths: list[str] = []
    for key in to_remove:
        skill = skills.pop(key, {})
        # Remove the on-disk skill directory if it exists
        slug = (
            normalize_key(skill.get("label", key))
            if isinstance(skill, dict)
            else normalize_key(key)
        )
        skill_dir = DATA_DIR / slug
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            removed_paths.append(str(skill_dir))

    save_skills_file(skills)

    return UninstallResponse(
        name=name,
        removed_path=removed_paths[0] if removed_paths else "",
        scope="user",
    )


@app.get("/v1/skills/{skill_id}/describe")
async def describe_skill(skill_id: str):
    """Describe a single installed skill in detail."""
    skills = load_skills_file()
    key = normalize_key(unquote(skill_id))
    # Check both prefixed and unprefixed keys
    skill = skills.get(f"openhuman_{key}") or skills.get(key)
    if not skill:
        # Try matching by label
        for k, v in skills.items():
            if isinstance(v, dict) and normalize_key(v.get("label", "")) == key:
                skill = v
                break

    if not skill:
        raise HTTPException(status_code=404, detail=f"skill '{skill_id}' not found")

    entry = CatalogEntry(
        id=key,
        name=skill.get("label", key),
        description=skill.get("canned_reply", ""),
        source=skill.get("source", "unknown"),
        category=skill.get("category", ""),
        tags=skill.get("tags", []),
        platforms=[],
        download_url=skill.get("download_url", ""),
        env_vars=skill.get("env_vars", []),
        commands=skill.get("commands", []),
    )
    return {"skill": entry.model_dump()}


@app.get("/v1/schemas")
async def skill_registry_schemas():
    """Return the controller schemas for all skill registry methods (for introspection)."""
    return {
        "schemas": [
            {
                "namespace": "skill_registry",
                "function": "browse",
                "description": "Browse the full OpenHuman skill catalog",
                "path": "/v1/catalog",
                "method": "GET",
                "params": {"force_refresh": "bool", "include_skills": "bool"},
            },
            {
                "namespace": "skill_registry",
                "function": "search",
                "description": "Search skills by keyword",
                "path": "/v1/catalog/search",
                "method": "GET",
                "params": {
                    "q": "string (required)",
                    "source": "string",
                    "category": "string",
                },
            },
            {
                "namespace": "skill_registry",
                "function": "install",
                "description": "Install a skill from the catalog",
                "path": "/v1/skills/install",
                "method": "POST",
                "params": {"entry_id": "string (required)"},
            },
            {
                "namespace": "skill_registry",
                "function": "uninstall",
                "description": "Uninstall an installed skill",
                "path": "/v1/skills/uninstall",
                "method": "POST",
                "params": {"name": "string (required)"},
            },
            {
                "namespace": "skill_registry",
                "function": "schemas",
                "description": "List available API schemas",
                "path": "/v1/schemas",
                "method": "GET",
                "params": {},
            },
        ]
    }


# ── Startup ───────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    """Warm the catalog cache on startup so the first browse is fast."""
    logger.info("OpenHuman bridge starting up")
    logger.info("  catalog_url: %s", CATALOG_URL)
    logger.info("  cache_dir:   %s", CACHE_DIR)
    logger.info("  skills_file: %s", SKILLS_FILE)
    logger.info("  data_dir:    %s", DATA_DIR)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Fire-and-forget a background cache warm (don't block startup on network)
    import asyncio

    asyncio.create_task(warm_cache())


async def warm_cache():
    """Background task to pre-fetch the catalog."""
    try:
        await get_catalog(force_refresh=False)
        logger.info("catalog cache warmed on startup")
    except Exception as e:
        logger.warning("background cache warm failed (non-fatal): %s", e)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8790"))
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)
