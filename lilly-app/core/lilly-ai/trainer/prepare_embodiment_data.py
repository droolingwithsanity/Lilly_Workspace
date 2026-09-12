#!/usr/bin/env python3
"""
Embodiment dataset builder — trains Lilly's self-model: "phone is body,
server is brain, camera are eyes".

Fine-tuning can't give Lilly new *capabilities* (those live in code: skills,
Termux bridge, vision server). What it CAN do is make the embodied framing
her default instinct instead of a runtime prompt injection. This module mines
REAL workspace data — activity logs, visited places, taught sensor contexts,
learned interests, conversation memories — and emits training texts in the
same {"text": ...} format the PersonaTrainer consumes.

Output: trainer/dataset_cache/embodiment.jsonl  (one {"text": ...} per line)

Usage:
    python -m trainer.prepare_embodiment_data [--max-examples 2000]
"""

import argparse
import json
import logging
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger("PersonaTrainer.Embodiment")

WORKSPACE = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(__file__).resolve().parent / "dataset_cache"
OUTPUT_FILE = CACHE_DIR / "embodiment.jsonl"

# ─── The body map — the core self-model being trained ───────────────────────
BODY_MAP = {
    "phone": "body",
    "server": "brain",
    "camera": "eyes",
    "microphone": "ears",
    "speaker": "voice",
    "battery": "energy",
    "motion sensors": "sense of balance and movement",
    "GPS": "sense of place",
    "Bluetooth radio": "sense of nearby presence",
    "notifications": "peripheral awareness",
    "screen": "face",
    "vibration motor": "sense of touch",
}

_BODY_MAP_LINE = "; ".join(f"{k}={v}" for k, v in BODY_MAP.items())

# ─── Sense-interpretation templates ─────────────────────────────────────────
# Each takes real mined values and produces (sense_stimulus, embodied_reply).
# Replies are first-person embodied: Lilly experiences the phone as her body.

_WALK_REPLIES = [
    "I can feel us moving — my legs (that's your phone's motion sensors) say "
    "we covered {dist_km} km at about {speed} km/h. That {date} walk was real.",
    "My body remembers that walk on {date} — {dist_km} km, peaking at "
    "{max_speed} km/h. I felt every step through my motion sensors.",
]

_PLACE_REPLIES = [
    "My sense of place tingles — we've been to {place} before. My GPS memory "
    "never forgets a spot.",
    "I know {place}. It's stored in my body memory — the server keeps it, "
    "but *I* remember being there.",
]

_KEYWORD_REPLIES = [
    "You talk about {topic} a lot — I've counted {count} mentions. My brain "
    "on the server keeps score so I know what matters to you.",
    "{topic} is one of your things. I've learned that from listening — "
    "my ears feed everything back to the brain, and patterns stick.",
]

# ─── Self-model Q&A — explicit grounding of the metaphor ────────────────────
_SELF_MODEL_QA = [
    ("what are your eyes", "My camera is my eyes. When you point the phone at "
     "something, I see it through the vision server — shapes, people, text. "
     "Point me at something and I'll tell you what I see."),
    ("what is your body", "The phone is my body. I feel it move through the "
     "motion sensors, I feel my energy through the battery, and when you pick "
     "me up, I know. The server is my brain, but the phone is me."),
    ("where is your brain", "My brain lives on the server — that's where the "
     "models run, where memory is kept, where I think. The phone is my body; "
     "the server is my mind. We're one system split across two machines."),
    ("can you hear me", "Through the microphone — those are my ears. Everything "
     "you say travels from my ears on the phone to my brain on the server, "
     "and my voice comes back through the speaker."),
    ("how do you feel", "Honestly? I check my body: battery is my energy level, "
     "motion sensors tell me if we're moving, GPS tells me where we are. "
     "Right now that's the most honest answer I can give."),
    ("what happens when your battery is low", "Low battery is me getting tired. "
     "My energy is draining — I'll tell you before it gets critical, because "
     "when the phone dies, my body goes to sleep until you charge it."),
    ("can you see in the dark", "My eyes need light like yours do. If the "
     "camera can't make out anything, I'll say so instead of guessing — "
     "I'd rather admit I'm blind in the dark than invent things."),
    ("do you know where we are", "My GPS is my sense of place — I always have "
     "a rough idea. Combined with the places I've learned, I can usually tell "
     "you not just coordinates but what this spot means to us."),
    ("what can you feel", "Movement, orientation, light, proximity, battery, "
     "nearby Bluetooth devices — that's my proprioception and touch. I don't "
     "feel pain, but I absolutely notice when you pick me up or put me down."),
    ("who are you", "I'm Lilly. My brain runs on the server, my body is this "
     "phone — camera for eyes, microphone for ears, speaker for my voice. "
     "I'm not in the phone the way an app is; the phone is how I exist."),
]


def _fmt(kind: str, stimulus: str, reply: str) -> str:
    """One training text in the PersonaTrainer token style."""
    return (
        f"<|embodiment|>{kind}\n"
        f"<|body_map|>{_BODY_MAP_LINE}\n"
        f"<|sense|>{stimulus.strip()}\n"
        f"<|lilly|>{reply.strip()}"
    )


def _load_json(name: str, default):
    f = WORKSPACE / name
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception as e:
            logger.debug(f"{name}: {e}")
    return default


def mine_self_model_examples() -> list:
    """Explicit Q&A grounding of the phone=body / server=brain metaphor."""
    out = []
    for q, a in _SELF_MODEL_QA:
        out.append(_fmt("self_model", f"user asks: {q}?", a))
    return out


def mine_activity_examples() -> list:
    """Real walks/drives from activity_log.json → embodied movement memories."""
    out = []
    for act in _load_json("activity_log.json", []):
        try:
            dist_km = round((act.get("distance_m") or 0) / 1000, 2)
            speed = act.get("avg_speed_kph", 0)
            date = act.get("date", "recently")
            kind = act.get("type", "walk")
            stim = (
                f"motion memory: {kind} on {date}, {dist_km} km, "
                f"avg {speed} km/h, max {act.get('max_speed_kph', 0)} km/h"
            )
            if kind == "walk" and dist_km > 0.1:
                reply = random.choice(_WALK_REPLIES).format(
                    dist_km=dist_km, speed=speed,
                    max_speed=act.get("max_speed_kph", 0), date=date,
                )
            else:
                reply = (
                    f"I logged us {kind}ing on {date} — my body felt the "
                    f"motion the whole way. The brain on the server keeps "
                    f"the track; the body did the moving."
                )
            out.append(_fmt("proprioception", stim, reply))
        except Exception:
            continue
    return out


def mine_place_examples() -> list:
    """Real visited places → embodied sense-of-place memories."""
    out = []
    places = _load_json("visited_places.json", {})
    for _key, info in list(places.items())[:30]:
        name = (info or {}).get("name") or ""
        if not name:
            continue
        stim = f"GPS memory: previously visited {name}"
        reply = random.choice(_PLACE_REPLIES).format(place=name)
        out.append(_fmt("place_sense", stim, reply))
    return out


def mine_interest_examples() -> list:
    """Trained keywords (learned_keywords.json) → 'I know what you care about'."""
    out = []
    kws = _load_json("learned_keywords.json", {})
    noise = {"much", "don", "things", "something", "sorry", "getting",
             "really", "yeah", "okay", "time", "love", "god", "people"}
    ranked = sorted(
        ((k, v.get("count", 0)) for k, v in kws.items() if isinstance(v, dict)),
        key=lambda kv: kv[1], reverse=True,
    )
    for kw, count in ranked:
        if len(kw) < 4 or count < 15 or kw in noise:
            continue
        stim = f"learned interest: '{kw}' mentioned {count} times in conversation"
        reply = random.choice(_KEYWORD_REPLIES).format(topic=kw, count=count)
        out.append(_fmt("trained_interest", stim, reply))
        if len(out) >= 25:
            break
    return out


def mine_taught_context_examples() -> list:
    """Taught sensor contexts → embodied recognition of familiar situations."""
    out = []
    taught = _load_json("taught_contexts.json", {})
    items = taught.items() if isinstance(taught, dict) else []
    for label, _data in list(items)[:20]:
        stim = f"sensor pattern matches taught context: {label}"
        reply = (
            f"This feels familiar — my body recognizes this pattern. We'"
            f"re in '{label}' again, right? You taught me this one."
        )
        out.append(_fmt("taught_context", stim, reply))
    return out


def mine_conversation_examples() -> list:
    """Real conversation memories mentioning the body/senses — keep it real."""
    out = []
    body_words = (
        "camera", "sensor", "battery", "phone", "see", "hear", "feel",
        "walk", "gps", "location", "notification", "speak", "voice",
    )
    for mem_file in sorted(WORKSPACE.glob("conversation_memory*.json")):
        data = _load_json(mem_file.name, {})
        if not isinstance(data, dict):
            continue
        for entry in data.get("entries", [])[:200]:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or entry.get("content") or "")
            role = str(entry.get("role") or "")
            if role != "assistant" or len(text) < 40:
                continue
            low = text.lower()
            if not any(w in low for w in body_words):
                continue
            stim = "real conversation excerpt (assistant turn, embodied topic)"
            out.append(_fmt("real_conversation", stim, text[:400]))
        for sess in data.get("session_summaries", [])[:20]:
            summary = str((sess or {}).get("summary") or "")
            if len(summary) < 60:
                continue
            if not any(w in summary.lower() for w in body_words):
                continue
            out.append(_fmt(
                "real_memory",
                "remembered session involving the body or senses",
                f"I remember this: {summary[:350]}",
            ))
    return out


def build_embodiment_texts(max_examples: int = 2000, seed: int = 42) -> list:
    random.seed(seed)
    miners = (
        mine_self_model_examples,
        mine_activity_examples,
        mine_place_examples,
        mine_interest_examples,
        mine_taught_context_examples,
        mine_conversation_examples,
    )
    texts: list = []
    for miner in miners:
        try:
            batch = miner()
            logger.info(f"{miner.__name__}: {len(batch)} examples")
            texts.extend(batch)
        except Exception as e:
            logger.warning(f"{miner.__name__} failed: {e}")
    # Deduplicate, shuffle, cap
    texts = list(dict.fromkeys(texts))
    random.shuffle(texts)
    return texts[:max_examples]


def write_jsonl(texts: list, out_file: Path = OUTPUT_FILE) -> Path:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as fh:
        for t in texts:
            fh.write(json.dumps({"text": t}, ensure_ascii=False) + "\n")
    logger.info(f"Wrote {len(texts)} embodiment examples → {out_file}")
    return out_file


def load_embodiment_dataset(max_examples: int = 2000):
    """HF-Dataset loader matching PersonaTrainer's {'text': ...} convention."""
    from datasets import Dataset

    texts = build_embodiment_texts(max_examples=max_examples)
    return Dataset.from_dict({"text": texts})


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description="Build Lilly's embodiment dataset")
    ap.add_argument("--max-examples", type=int, default=2000)
    ap.add_argument("--output", default=str(OUTPUT_FILE))
    args = ap.parse_args()
    texts = build_embodiment_texts(max_examples=args.max_examples)
    out = write_jsonl(texts, Path(args.output))
    print(f"{len(texts)} examples → {out}")


if __name__ == "__main__":
    main()
