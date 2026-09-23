#!/usr/bin/env python3
"""Register a trained avatar persona as an Ollama model and wire it into the
live Instagram avatars.

The avatars in lilly_pup_insta.py pick their Ollama model from
data/pup_insta/avatar_models.json (avatar key -> Ollama model name). This
script builds that Ollama model from a trained persona checkpoint and records
the mapping, so the trained voice is actually used for captions/comments/DMs.

Examples:
    python3 -m trainer.deploy_persona --list
    python3 -m trainer.deploy_persona --avatar puppy                # auto-find GGUF
    python3 -m trainer.deploy_persona --avatar fox --gguf trained_avatars/fox/gguf/model.gguf
    python3 -m trainer.deploy_persona --avatar fox --adapter trained_avatars/fox/lora.gguf --base qwen2.5:3b
    python3 -m trainer.deploy_persona --avatar fox --register-only --model lilly-fox
    python3 -m trainer.deploy_persona --unregister fox
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAINED_DIR = ROOT / "trained_avatars"
REGISTRY = Path(
    os.environ.get("AVATAR_MODEL_MAP", ROOT / "data" / "pup_insta" / "avatar_models.json")
)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_BASE = os.environ.get("PERSONA_OLLAMA_BASE", "qwen2.5:3b")

FALLBACK_KEYS = {"puppy", "fox", "cat", "bear", "bunny", "owl", "deer", "wolf", "raccoon"}


def _avatar_keys() -> set:
    try:
        sys.path.insert(0, str(ROOT))
        import lilly_pup_insta as lpi  # noqa: WPS433

        return set(lpi.AVATAR_INSTA_PERSONAS.keys())
    except Exception:
        return FALLBACK_KEYS


def _load_registry() -> dict:
    try:
        data = json.loads(REGISTRY.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registry(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _find_gguf(key: str):
    d = TRAINED_DIR / key / "gguf"
    if d.is_dir():
        ggufs = sorted(d.glob("*.gguf"))
        if ggufs:
            return ggufs[0]
    return None


def _create_model(model: str, modelfile: Path, dry: bool):
    ollama = shutil.which("ollama") or "ollama"
    cmd = [ollama, "create", model, "-f", str(modelfile)]
    if dry:
        return True, "DRY RUN: " + " ".join(cmd)
    if not shutil.which("ollama"):
        return False, (
            "ollama CLI not found on PATH — install/run it and retry, "
            "or use --register-only after creating the model another way"
        )
    env = dict(os.environ, OLLAMA_HOST=OLLAMA_HOST)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "ollama create failed").strip()
    return True, (proc.stdout or "created").strip()


def deploy(args) -> int:
    key = args.avatar
    keys = _avatar_keys()
    if keys and key not in keys:
        print(f"unknown avatar {key!r}; known: {', '.join(sorted(keys))}")
        return 2
    model = args.model or f"lilly-{key}"
    registry = _load_registry()

    if args.register_only:
        registry[key] = model
        _save_registry(registry)
        print(f"registered {key} -> {model} (no Ollama create)")
        return 0

    if args.adapter:
        src = Path(args.adapter)
        if not src.is_file():
            print(f"adapter not found: {src}")
            return 2
        modelfile_body = f"FROM {args.base or DEFAULT_BASE}\nADAPTER {src}\n"
    else:
        gguf = Path(args.gguf) if args.gguf else _find_gguf(key)
        if not gguf or not gguf.is_file():
            print(
                f"no GGUF for {key!r}. Train with save_gguf (GPU/Unsloth) or point "
                f"--gguf/--adapter at one. Merged/LoRA checkpoints under "
                f"{TRAINED_DIR / key} are not directly loadable by Ollama."
            )
            return 2
        modelfile_body = f"FROM {gguf}\n"

    with tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False) as fh:
        fh.write(modelfile_body)
        modelfile = Path(fh.name)

    try:
        ok, msg = _create_model(model, modelfile, args.dry_run)
    finally:
        modelfile.unlink(missing_ok=True)
    if not ok:
        print(f"failed to create {model}: {msg}")
        return 1
    print(msg)
    if args.dry_run:
        print(f"(dry-run) would register {key} -> {model}")
        return 0
    registry[key] = model
    _save_registry(registry)
    print(f"registered {key} -> {model} in {REGISTRY}")
    return 0


def unregister(key: str) -> int:
    registry = _load_registry()
    if key in registry:
        del registry[key]
        _save_registry(registry)
        print(f"unregistered {key}")
    else:
        print(f"{key} was not registered")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--avatar")
    ap.add_argument("--model")
    ap.add_argument("--base")
    ap.add_argument("--gguf")
    ap.add_argument("--adapter")
    ap.add_argument("--register-only", action="store_true")
    ap.add_argument("--unregister", metavar="KEY")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        reg = _load_registry()
        print(json.dumps(reg, indent=2, sort_keys=True) if reg else "(registry empty)")
        return 0
    if args.unregister:
        return unregister(args.unregister)
    if not args.avatar:
        ap.error("--avatar is required (or use --list / --unregister)")
    return deploy(args)


if __name__ == "__main__":
    raise SystemExit(main())
