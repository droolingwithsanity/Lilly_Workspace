"""Google Drive integration — OAuth folder creation per department.

Setup:
  1. Go to https://console.cloud.google.com/ -> Enable Google Drive API
  2. Create OAuth 2.0 credentials (Web application type)
  3. Set redirect URI to http://YOUR_HOST/api/drive/callback
  4. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET env vars
  5. Or use GOOGLE_DRIVE_TOKEN for an existing access token
"""

import os
import time
import json
import requests
from urllib.parse import urlencode

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "/api/drive/callback")

TOKEN = None  # in-memory token storage


def get_auth_url() -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive.file",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    global TOKEN
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    data = resp.json()
    TOKEN = data
    return data


def refresh_token() -> bool:
    global TOKEN
    if not TOKEN or "refresh_token" not in TOKEN:
        return False
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": TOKEN["refresh_token"],
        "grant_type": "refresh_token",
    })
    if resp.status_code == 200:
        new = resp.json()
        TOKEN["access_token"] = new["access_token"]
        return True
    return False


def _headers() -> dict:
    global TOKEN
    if not TOKEN or "access_token" not in TOKEN:
        return {}
    if TOKEN.get("expires_at", 0) < time.time() - 60:
        refresh_token()
    return {"Authorization": f"Bearer {TOKEN['access_token']}"}


def is_authenticated() -> bool:
    return bool(TOKEN and "access_token" in TOKEN)


def get_or_create_folder(name: str, parent_id: str = None) -> str:
    """Find or create a Google Drive folder by name. Returns folder ID."""
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    resp = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        params={"q": query, "fields": "files(id,name)"},
        headers=_headers(),
    )
    if resp.status_code != 200:
        return ""

    files = resp.json().get("files", [])
    if files:
        return files[0]["id"]

    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        body["parents"] = [parent_id]

    resp = requests.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={**_headers(), "Content-Type": "application/json"},
        json=body,
    )
    if resp.status_code == 200:
        return resp.json().get("id", "")
    return ""


def ensure_department_folders(departments: list[dict]) -> dict:
    """Create LillyOS root folder + one subfolder per department. Returns {dept_id: folder_id}."""
    if not is_authenticated():
        return {}

    root_id = get_or_create_folder("LillyOS")
    if not root_id:
        return {}

    result = {}
    for dept in departments:
        fid = get_or_create_folder(dept["name"], parent_id=root_id)
        result[dept["id"]] = fid
    return result


def get_folder_links(folder_map: dict, departments: list[dict]) -> list[dict]:
    """Return [{dept_id, name, url, folder_id}] for the frontend."""
    result = []
    dept_lookup = {d["id"]: d for d in departments}
    for dept_id, folder_id in folder_map.items():
        dept = dept_lookup.get(dept_id, {})
        result.append({
            "dept_id": dept_id,
            "name": dept.get("name", dept_id),
            "folder_id": folder_id,
            "url": f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else None,
        })
    return result
