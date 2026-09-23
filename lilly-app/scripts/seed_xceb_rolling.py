import sys, json, base64

sys.path.insert(0, "/app")
import xceb_faces, xceb_core


def tell(*a):
    line = " ".join(str(x) for x in a) + "\n"
    with open("/tmp/xceb_seed.log", "a") as f:
        f.write(line)
    print(line, end="", flush=True)


# 0) real crop bytes
raw = open("/tmp/crops/c.jpg", "rb").read()
b64 = base64.b64encode(raw).decode()
tell("CROP_BYTES=", len(raw))

# 1) embed via the SAME function vision uses (returns (embedding_list|None, box))
emb, box = xceb_faces.embed_crop_bytes(raw)
tell("EMBED=", "len=" + str(len(emb)) if emb else "None", "box=", str(box)[:40])

# 2) create the case via public API — signature confirmed above
case = xceb_core.create_case(
    source="vision_test",
    face_id="uf_xceb_ada",
    crop_b64=b64,
    original_b64=b64,
    geo_hint="London",
    category="social",
    note="Smoke-test seed — remove after verification",
    meta={"best_name": "Ada Lovelace Test", "confidence": 0.62},
)
cid = case.get("case_id")
tell("CASE_ID=", cid, "slug=", case.get("slug"))

# 3) enroll face linked to the case — public add_face
rec = xceb_faces.add_face(
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
tell(
    "FACE_ID=",
    rec.get("fid") if rec else None,
    "name=",
    rec.get("name") if rec else "",
    "case_ids=",
    rec.get("case_ids") if rec else "",
)

# 4) verify ledgers
led = xceb_faces._load_ledger()
tell("LEDGER_FACES=", len(led.get("faces", [])))
cat = xceb_core.catalog_list()
tell("CATALOG_CASES=", len(cat) if isinstance(cat, list) else "?")
if isinstance(cat, list):
    for m in cat[:3]:
        tell(
            "  case=",
            m.get("case_id"),
            m.get("status"),
            m.get("best_name"),
            m.get("confidence"),
        )
tell("DONE")
