import sys, json, base64

sys.path.insert(0, "/app")
import xceb_faces as F, xceb_core as C


def tell(*a):
    line = " ".join(str(x) for x in a)
    with open("/tmp/seed_out.txt", "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


# 1) embed the real crop (vision-quality pipeline: embed bytes)
raw = open("/tmp/crops/c.jpg", "rb").read()
emb, box = F.embed_crop_bytes(raw)
tell(
    "EMBED",
    type(emb).__name__ if emb is not None else None,
    len(emb) if emb is not None else 0,
)

b64img = base64.b64encode(raw).decode()

# 2) create the case (public API)
case = C.create_case(
    source="vision_test",
    face_id="uf_xceb_ada",
    crop_b64=b64img,
    original_b64=b64img,
    geo_hint="London",
    category="social",
    note="Smoke-test seed — will be removed after verification",
    meta={"best_name": "Ada Lovelace Test", "confidence": 0.62},
)
cid = case["case_id"]
tell("CASE", cid, case["slug"])

# 3) enroll the face linked to the case
from_deep = C._findings_of(case) if hasattr(C, "_findings_of") else {}
rec = F.add_face(
    embedding=emb,
    name="Ada Lovelace Test",
    source="case",
    thumb_b64=b64img,
    confidence=0.62,
    category="social",
    platform="linkedin",
    url="https://linkedin.com/in/ada-lovelace-test",
    case_id=cid,
)
tell(
    "FACE",
    rec and rec["fid"],
    rec
    and (
        len(rec.get("case_ids", []))
        if isinstance(rec.get("case_ids"), list)
        else rec.get("case_ids")
    ),
)
tell("IS_ASCII_STUB", "USED REAL add_face")

# 4) verify ledger persistence + catalog
led = F._load_ledger()
tell("LEDGER_FACES", len(led.get("faces", [])))
casts = C.catalog_list()
tell("CATALOG", len(casts))
for m in casts[:4]:
    tell("  meta", m.get("case_id"), m.get("best_name"), m.get("status"))
