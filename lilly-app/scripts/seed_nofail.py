import sys, json, base64, time

sys.path.insert(0, "/app")
import xceb_faces, xceb_core


def tell(*a):
    line = "SEED " + " ".join(str(x) for x in a) + "\n"
    with open("/tmp/seed_log.txt", "a") as f:
        f.write(line)
    sys.stdout.flush()


# real crop bytes (vision-quality crop the reactor actually uses)
raw = open("/tmp/c.jpg", "rb").read()
tell("CROP_BYTES", len(raw))
b64 = base64.b64encode(raw).decode()

# 1) embed via the SAME path
res = xceb_faces.embed_crop_bytes(raw)
emb = res[0]
box = res[1]
tell("EMBED", "emb=", (len(emb) if emb else 0), "box=", (box and str(box)[:40] or ""))

# 2) create case (source/vision pipeline semantics)
case = xceb_core.create_case(
    source="vision",
    face_id="uf_xceb_ada",
    crop_b64=b64,
    original_b64=b64,
    geo_hint="London",
    category="social",
    note="Seed smoke",
    meta={"everything": True},
)
tell("CASE_KEYS", json.dumps(sorted(case.keys()))[:300])
cid = case.get("case_id")
slug = case.get("slug")
tell("CID", cid, "| slug", slug)

# 3) add face linked to the case
rec = xceb_faces.add_face(
    embedding=emb,
    name="Ada Lovelace Test",
    source="case",
    thumb_b64=b64,
    geo={"city": "London"},
    category="social",
    platform="linkedin",
    url="https://linkedin.com/in/ada-lovelace-test",
    case_id=cid,
    confidence=0.62,
)
tell("FACE_REC", json.dumps(rec, default=str)[:300] if rec else "None")

# 4) verify ledgers
faces = xceb_faces._load_ledger().get("faces", [])
tell("LEDGER_FACES", len(faces))
try:
    cat = xceb_core.catalog()
    tell(
        "CATALOG",
        "len" if hasattr(cat, "__len__") else type(cat).__name__,
        len(cat) if hasattr(cat, "__len__") else "",
    )
except Exception as e:
    tell("CATALOG_ERR", type(e).__name__, str(e)[:120])
tell("DONE")
