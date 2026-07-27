#!/usr/bin/env python3
"""
Lilly File Share Server — runs alongside the main server on port 8099.
Upload, download, and list files from the share directory.

Run:
  python3 file_share_server.py --port 8099 --dir ./file_share

The main server (lilly_ai.py) also mirrors these endpoints at /api/files/* on port 8098.
"""
import os, sys, json, re, logging
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FileShareServer")

PORT = int(os.environ.get("FILE_SHARE_PORT", "8099"))
SHARE_DIR = Path(os.environ.get("FILE_SHARE_DIR", os.path.join(os.path.dirname(__file__), "file_share")))

app = FastAPI(title="Lilly File Share")

@app.on_event("startup")
async def startup():
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"File share directory: {SHARE_DIR}")

@app.get("/api/files/list")
async def list_files():
    files = []
    for f in SHARE_DIR.iterdir():
        if f.is_file():
            files.append({"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime})
    return JSONResponse(files)

@app.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    safe_name = re.sub(r'[^\w\.\-]', '_', file.filename)
    dest = SHARE_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    logger.info(f"Uploaded: {safe_name} ({len(content)} bytes)")
    return {"status": "ok", "filename": safe_name, "size": len(content)}

@app.get("/api/files/{filename}")
async def download_file(filename: str):
    safe_name = re.sub(r'[^\w\.\-]', '_', filename)
    path = SHARE_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), filename=safe_name)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lilly File Share Server")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port (default {PORT})")
    parser.add_argument("--dir", type=str, default=str(SHARE_DIR), help="Share directory")
    args = parser.parse_args()
    PORT = args.port
    SHARE_DIR = Path(args.dir)
    logger.info(f"Starting file share server on port {PORT}, directory: {SHARE_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
