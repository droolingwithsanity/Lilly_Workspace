import sys, json, inspect

sys.path.insert(0, "/app")
import xceb_faces, xceb_core

rows = ["INSPECT START"]
rows.append("add_face sig: " + str(inspect.signature(xceb_faces.add_face)))
rows.append("create_case sig: " + str(inspect.signature(xceb_core.create_case)))
rows.append("embed fns: " + json.dumps([f for f in dir(xceb_faces) if "embed" in f]))
rows.append(
    "faces module attrs: "
    + json.dumps([f for f in dir(xceb_faces) if not f.startswith("_")][:40])
)

# show current ledger structure (this module writes it)
led = xceb_faces._load_ledger()
rows.append("ledger keys: " + json.dumps([k for k in led.keys()]))
for f in led.get("faces", [])[:8]:
    rows.append(
        "face: "
        + json.dumps(
            {k: f.get(k) for k in ("fid", "name", "case_ids", "source")}, default=str
        )[:200]
    )

# catalog view
try:
    cat = xceb_core.catalog_list()
    rows.append(
        "catalog type: "
        + type(cat).__name__
        + " len-ish: "
        + str(len(cat) if hasattr(cat, "__len__") else "?")
    )
    rows.append(
        "catalog sample: " + json.dumps(cat[:1800], default=str)[:1800]
        if isinstance(cat, list)
        else str(cat)[:1800]
    )
except Exception as e:
    rows.append("catalog ERR: " + type(e).__name__ + " " + str(e)[:200])

open("/tmp/inspect_out.txt", "w").write("\n".join(rows))
