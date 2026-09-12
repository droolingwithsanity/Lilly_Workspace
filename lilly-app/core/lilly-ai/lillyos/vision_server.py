"""YOLO object detection server.

Runs Ultralytics YOLOv8 via FastAPI, listens on port 8095.
Accepts JPEG frames as raw bytes or as base64-encoded JSON.

Set YOLO_MODEL to override the default model (default: yolov8n.pt).
"""

import io
import os
import time

import numpy as np
import uvicorn
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import JSONResponse
from ultralytics import YOLO

MODEL_PATH = os.environ.get("YOLO_MODEL", "yolov8n.pt")
PORT = int(os.environ.get("YOLO_PORT", "8095"))
HOST = os.environ.get("YOLO_HOST", "0.0.0.0")

print(f"Loading YOLO model: {MODEL_PATH} ...", flush=True)
_model = YOLO(MODEL_PATH)
print("YOLO model loaded.", flush=True)

app = FastAPI(title="Lilly YOLO Vision Server", version="1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_PATH, "ts": time.time()}


@app.get("/api/vision")
async def vision_get():
    return {"status": "ok", "model": MODEL_PATH, "endpoints": ["/api/vision", "/api/vision/frame"]}


def _run_detection(img) -> list[dict]:
    """Run YOLO on an OpenCV image, return detection list."""
    results = _model(img, verbose=False)[0]
    detections = []
    for box in results.boxes:
        cls = int(box.cls[0])
        label = _model.names[cls] if cls < len(_model.names) else f"class_{cls}"
        conf = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        detections.append({
            "label": label,
            "confidence": round(conf, 3),
            "conf": round(conf, 3),
            "x": int(x1),
            "y": int(y1),
            "w": int(x2 - x1),
            "h": int(y2 - y1),
        })
    return detections


async def _extract_frame_bytes(request: Request) -> bytes | None:
    """Extract JPEG bytes from raw body, multipart file, or JSON image_b64."""
    import cv2
    import base64

    content_type = request.headers.get("Content-Type", "")

    if "application/json" in content_type:
        body = await request.body()
        import json
        try:
            payload = json.loads(body)
            b64 = payload.get("image_b64") or payload.get("frame_b64")
            if b64:
                return base64.b64decode(b64)
        except Exception:
            pass
        return None

    # Try multipart UploadFile
    try:
        form = await request.form()
        f = form.get("file")
        if f and hasattr(f, "read"):
            return await f.read()
    except Exception:
        pass

    # Raw bytes body
    return await request.body()


@app.post("/api/vision")
async def vision_post(request: Request):
    """Accept JPEG as raw bytes, multipart, or JSON image_b64. Return detections + annotated frame."""
    import cv2

    frame_bytes = await _extract_frame_bytes(request)
    if not frame_bytes:
        return JSONResponse({"error": "No frame data"}, status_code=400)

    try:
        nparr = np.frombuffer(frame_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return JSONResponse({"error": "Invalid image data"}, status_code=400)

        detections = _run_detection(img)

        # Return annotated frame
        annotated = _model(img, verbose=False)[0].plot()
        _, jpeg = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 75])

        return JSONResponse({
            "ok": True,
            "detections": detections,
            "frame": list(jpeg.tobytes()),
            "width": int(img.shape[1]),
            "height": int(img.shape[0]),
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/vision/frame")
async def vision_frame(request: Request):
    """Lightweight POST — return detections only (no annotated image)."""
    import cv2

    frame_bytes = await _extract_frame_bytes(request)
    if not frame_bytes:
        return JSONResponse({"error": "No frame data"}, status_code=400)

    try:
        nparr = np.frombuffer(frame_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return JSONResponse({"error": "Invalid image data"}, status_code=400)

        detections = _run_detection(img)
        return JSONResponse({"ok": True, "detections": detections})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
