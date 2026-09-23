import sys, json, base64, os

sys.path.insert(0, "/app")
import xceb_faces, xceb_core

LOG = "/tmp/seed_final.txt"


def tell(*a, **k):
    line = (
        "SEED "
        + " ".join(str(x) for x in a)
        + (" " + json.dumps(k, default=str)[:160] if k else "")
    )
    with open(LOG, "a") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:
        pass


# 1) embed the real crop through the SAME path vision uses
raw = open("/tmp/crops/c.jpg", "rb").read()
b64 = base64.b64encode(raw).decode()
emb, box = xceb_faces.embed_crop_bytes(raw)
tell("EMBED", "len=" + str(len(emb) if emb else 0), "box=" + str(box)[:40])

# 2) create the case (public API: source, face_id, crop_b64, original_b64, geo_hint, category, note, note?, meta)
case = xceb_core.create_case(
    source="case",
    face_id="uf_xceb_ada",
    crop_b64=b64,
    original_b64=b64,
    geo_hint="London",
    category="social",
    note="Smoke-test seed — remove after verification",
    meta={"best_name": "Ada Lovelace Test", "confidence": 0.62},
)
tell("CASE", json.dumps(case, default=str)[:220])
cid = case.get("case_id") if isinstance(case, dict) else None
tell("CID", cid)

# 3) enroll face linked to the case
if cid:
    face = xceb_faces.add_face(
        embedding=emb,
        name="Ada Lovelace Test",
        source="case",
        thumb_b64=b64,
        confidence=0.62,
        category="social",
        platform="linkedin",
        url="https://www.linkedin.com/in/ada-lovelace-test",
        case_id=cid,
    )
    tell("FACE", json.dumps(face, default=str)[:260] if face else "None")

# 4) verify
faces = xceb_faces._load_ledger().get("faces", [])
tell("LEDGER_FACES", len(faces))
cat = xceb_core.catalog_list()
tell("CATALOG", len(cat) if hasattr(cat, "__len__") else "n/a")
try:
    cat0 = cat[0] if isinstance(cat, list) and cat else None
    tell("CAT0", json.dumps(cat0, default=str)[:200] if cat0 else "EMPTY")
except Exception as e:
    tell("CAT0_ERR", type(e).__name__, str(e)[:120])
tell("FIN")
