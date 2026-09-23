import sys, json, base64

sys.path.insert(0, "/app")
import xceb_faces as F, xceb_core as C

LOG = "/tmp/xceb_ins.jsonl"


def tell(**k):
    with open(LOG, "a") as f:
        f.write(json.dumps(k, default=str) + "\n")
    print(json.dumps(k, default=str), flush=True)


# real crop file (must exist in container)
raw = open("/tmp/crops/c.jpg", "rb").read()
b64 = base64.b64encode(raw).decode()

# 1) embed via the real vision path
emb, box = F.embed_crop_bytes(raw)
tell(step="EMBED", emb_len=len(emb) if emb else 0, box=str(box)[:40])

# 2) create case through public API
case = C.create_case(
    source="vision_test",
    face_id="uf_xceb_ada2",
    crop_b64=b64,
    original_b64=b64,
    geo_hint="London",
    category="social",
    note="Seed smoke-test (remove after verify)",
    meta={"best_name": "Ada Lovelace Test", "confidence": 0.62},
)
tell(step="CASE", case_id=case.get("case_id"), slug=case.get("slug"))

# 3) enroll face linked to case
rec = F.add_face(
    embedding=emb,
    name="Ada Lovelace Test",
    source="case",
    thumb_b64=b64,
    confidence=0.62,
    category="social",
    platform="linkedin",
    url="https://linkedin.com/in/ada-lovelace-test",
    case_id=case.get("case_id"),
)
tell(
    step="FACE",
    fid=rec.get("fid") if rec else None,
    name=rec.get("name") if rec else None,
    case_ids=rec.get("case_ids") if rec else None,
)

# 4) verify ledgers persisted
fires = F._load_ledger().get("faces", [])
tell(step="LEDGER", n=len(fires))
cat = C.catalog_list()
tell(step="CATALOG", n=len(cat) if isinstance(cat, list) else "n/a")
tell(step="DONE")
