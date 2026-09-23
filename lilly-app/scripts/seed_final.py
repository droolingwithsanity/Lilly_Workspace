import sys, json, base64, re

sys.path.insert(0, "/app")
import xceb_faces, xceb_core


def tell(*a):
    msg = "SEED " + " ".join(str(x) for x in a) + "\n"
    with open("/tmp/seed_err.txt", "a") as f:
        f.write(msg)
    print(msg, end="", flush=True)


# sign/verify public API contract quickly at runtime
tell(
    "add_face sig:",
    json.dumps({k: str(v) for k, v in xceb_faces.add_face.__defaults__ or []}),
)
