import sys, json, base64

sys.path.insert(0, "/app")
import xceb_core, xceb_faces


def tell(x):
    s = str(x)
    sys.stderr.write("SEED " + s + "\n")
    sys.stderr.flush()


b64 = base64.b64encode(open("/tmp/crops/c.jpg", "rb").read()).decode()
emb = xceb_faces.embed_crop_bytes(b64)
tell("EMBED len=" + str(len(emb) if emb is not None else None))

# 1) public create_case API
case = xceb_core.create_case(
    source="vision_test",
    face_id="uf_xceb_ada",
    geo_hint="London",
    category="social",
    note="Smoke-test seed (removed after verification)",
    meta={"best_name": "Ada Lovelace Test", "confidence": 0.62},
)
tell("CASE=" + json.dumps(case, default=str)[:200])
cid = case.get("case_id") if isinstance(case, dict) else None
tell("CID=" + str(cid))

# 2) public add_face linked to the case
face = xceb_faces.add_face(
    embedding=emb,
    name="Ada Lovelace Test",
    source="case",
    thumb_b64=b64,
    confidence=0.62,
    category="social",
    case_id=cid,
    platform="linkedin",
    url="https://linkedin.com/in/ada-lovelace-test",
)
tell("FACE=" + json.dumps(dict(face), default=str)[:220])

# 3) verify ledgers persisted
led = xceb_faces._load_ledger()
tell("LEDGER faces=" + str(len(led.get("faces", []))))
cat = xceb_core.catalog_list()
tell(
    "CATALOG="
    + (
        json.dumps(cat[:2], default=str)[:220]
        if isinstance(cat, list)
        else str(cat)[:220]
    )
)

# 4) locate brief builder to confirm route target
br = [n for n in dir(xceb_core) if "brief" in n.lower()]
tell("BRIEF_FNS=" + str(br))
tell("DONE")
