import sys, json, base64

sys.path.insert(0, "/app")
import xceb_faces as F, xceb_core as C


def tell(*a):
    line = "SEED " + " ".join(str(x) for x in a) + "\n"
    open("/tmp/final_seed.log", "a").write(line)
    print(line.strip(), flush=True)


# 1) real crop bytes -> embedding (same path vision uses)
raw = open("/tmp/crops/c.jpg", "rb").read()
emb, box = F.embed_crop_bytes(raw)
tell("EMB_LEN", len(emb) if emb is not None else 0, "BOX", str(box)[:60])
b64 = base64.b64encode(raw).decode()

# 2) create case (public API)
case = C.create_case(
    source="vision_test",
    face_id="uf_xceb_ada2",
    crop_b64=b64,
    original_b64=b64,
    geo_hint="London",
    category="social",
    note="Seed smoke-test (self-cleaning — removed after verify)",
    meta={"best_name": "Ada Lovelace Test", "confidence": 0.62},
)
tell("CASE", json.dumps(case, default=str)[:220])
cid = case.get("case_id") if isinstance(case, dict) else None
tell("CID", cid)

# 3) enroll face linked to case
face = F.add_face(
    embedding=emb,
    name="Ada Lovelace Test",
    source="case",
    thumb_b64=b64,
    geo={"city": "London"},
    category="social",
    platform="linkedin",
    url="https://linkedin.com/in/ada-lovelace-test-smoke",
    case_id=cid,
    confidence=0.62,
)
tell("FACE", json.dumps(face, default=str)[:260] if face else "None")

# 4) verify ledgers
faces = F._load_ledger().get("faces", [])
tell("LEDGER_FACES", len(faces))
cat = C.catalog_list()
tell("CATALOG", len(cat) if hasattr(cat, "__len__") else str(cat)[:80])
if isinstance(cat, list) and cat:
    tell("CAT0", json.dumps(cat[0], default=str)[:260])
tell("DONE")
