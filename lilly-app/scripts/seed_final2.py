import sys, json, base64

sys.path.insert(0, "/app")
import xceb_faces, xceb_core

# redirect everything to /tmp/xceb_seed.log
_log = open("/tmp/xceb_seed.log", "a")


def tell(*a):
    _log.write(" ".join(str(x) for x in a) + "\n")
    _log.flush()


# 1) embed the real crop via the SAME embed fn vision uses
raw = open("/tmp/crops/c.jpg", "rb").read()
emb, box = xceb_faces.embed_crop_bytes(raw)
tell("EMBED", "len=" + str(len(emb) if emb else 0), "box=" + str(box)[:60])

if not emb:
    tell("EMBED_FAIL", "no embedding — abort")
    sys.exit(2)

b64 = base64.b64encode(raw).decode()

# 2) create a case (public API, returns dict with case_id)
res = xceb_core.create_case(
    source="vision_test",
    face_id="uf_xceb_ada",
    crop_b64=b64,
    original_b64=b64,
    geo_hint="London",
    category="social",
    note="Smoke-test seed — to be removed after verification",
    meta={"name": "Ada Lovelace Test", "confidence": 0.62},
)
tell("CREATE_CASE", json.dumps(res, default=str)[:300])
cid = res.get("case_id") if isinstance(res, dict) else None
tell("CID", cid)

# 3) enroll face linked to case
if cid:
    face = xceb_faces.add_face(
        embedding=emb,
        name="Ada Lovelace Test",
        source="case",
        thumb_b64=b64,
        confidence=0.62,
        category="social",
        platform="linkedin",
        url="https://linkedin.com/in/ada-lovelace-test",
        case_id=cid,
    )
    tell("FACE", json.dumps(face, default=str)[:300] if face else None)

# 4) write a resolved-shaped case dir for the brief profile
case_dir = None
if cid:
    # find the case dir (bind: /app/data/cases/<slug>)
    import os

    for d in os.listdir("/app/data/cases"):
        cd = os.path.join("/app/data/cases", d)
        cf = os.path.join(cd, "case.json")
        if os.path.isfile(cf):
            try:
                c = json.load(open(cf))
                if c.get("case_id") == cid:
                    case_dir = cd
                    break
            except Exception:
                pass
tell("CASE_DIR", case_dir)

# 5) verify ledgers
led = xceb_faces._load_ledger()
tell("LEDGER_FACES", len(led.get("faces", [])))
cat = xceb_core.catalog_list()
tell("CATALOG_LEN", len(cat) if isinstance(cat, list) else str(type(cat)))
tell(
    "CATALOG_0",
    json.dumps(cat[0], default=str)[:300] if isinstance(cat, list) and cat else "",
)
tell("DONE_SEED")
