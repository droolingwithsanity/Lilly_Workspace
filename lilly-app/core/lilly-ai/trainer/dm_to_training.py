#!/usr/bin/env python3
"""
dm_to_training.py — Convert Instagram DM conversations to persona training format.
Reads from handled.json / interactions.json and produces training data
compatible with the persona LoRA trainer (<|persona|><|conversation|> format).

Usage:
    python3 dm_to_training.py --avatar wolf --output /path/to/output.jsonl
    python3 dm_to_training.py --all --output-dir /path/to/output/
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

WORKER_DIR = Path("/home/xceb/dm-worker")
HANDLED_FILE = WORKER_DIR / "handled.json"
INTERACTIONS_FILE = WORKER_DIR / "interactions.json"
PERSONAS_FILE = WORKER_DIR / "personas.json"
CREDENTIALS_FILE = WORKER_DIR / "credentials.json"


def load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default if default is not None else {}


def load_personas() -> Dict:
    data = load_json(PERSONAS_FILE, {})
    return data.get("personas", data)


def load_interactions() -> List[Dict]:
    data = load_json(INTERACTIONS_FILE, {"items": []})
    return data.get("items", [])


def load_handled() -> Dict:
    return load_json(HANDLED_FILE, {"seen": [], "messages": {}})


def get_persona_info(avatar_key: str, personas: Dict) -> Optional[Dict]:
    persona = personas.get(avatar_key, {})
    if not persona:
        return None
    return {
        "name": persona.get("name", avatar_key.title()),
        "emoji": persona.get("emoji", ""),
        "ig_handle": persona.get("ig_handle", ""),
        "role": persona.get("role", ""),
        "reply_vibe": persona.get("reply_vibe", ""),
        "style": persona.get("style", ""),
        "bio": persona.get("bio", ""),
        "speech_patterns": persona.get("speech_patterns", []),
    }


def format_persona_training(
    avatar_key: str,
    conversations: List[List[Tuple[str, str]]],
    personas: Dict,
) -> List[str]:
    """
    Convert DM conversations to persona training format.

    Each conversation is a list of (speaker, text) tuples.
    Output format matches the persona trainer's <|persona|> markers.

    Example output:
        <|persona|>wolf
        <|background|>Calm in a crisis...
        <|style|>Short, direct lines...
        <|conversation|>
        <|user|>hey wolf what are you up to
        <|wolf|>running. don't disturb.
        <|user|>that was a sick post
        <|wolf|>that one's mine actually.
        <|wolf|>
    """
    persona_info = get_persona_info(avatar_key, personas)
    if not persona_info:
        return []

    training_examples = []
    name = persona_info["name"]

    for convo in conversations:
        parts = []
        parts.append(f"<|persona|>{avatar_key}")
        parts.append(f"<|background|>{persona_info.get('bio', '')}")
        parts.append(f"<|style|>{persona_info.get('style', '')}")
        parts.append(f"<|markers|>{', '.join(persona_info.get('speech_patterns', []))}")
        parts.append("<|conversation|>")

        for i, (speaker, text) in enumerate(convo):
            if speaker == name.lower() or speaker == avatar_key.lower():
                role = name
            else:
                role = "user"
            if text.strip():
                parts.append(f"<|{role}|>{text.strip()}")

        parts.append(f"<|{name}|>")
        training_examples.append("\n".join(parts))

    return training_examples


def build_conversations_from_interactions(
    interactions: List[Dict],
) -> Dict[str, List[List[Tuple[str, str]]]]:
    """
    Build conversations from interaction log entries.

    Groups interactions by (avatar, sender) and creates alternating
    conversation threads.
    """
    # Group by (avatar, sender)
    threads: Dict[str, List[List[Tuple[str, str]]]] = {}

    for item in interactions:
        avatar = item.get("avatar", "")
        username = item.get("username", "unknown")
        direction = item.get("direction", "in")
        text = item.get("text", "")
        ts = item.get("ts", 0)

        if not text or not avatar:
            continue

        key = f"{avatar}:{username}"
        if key not in threads:
            threads[key] = []

        # Find or create the current conversation thread
        if not threads[key] or threads[key][-1][-1][0] == username and direction == "out":
            # Start a new conversation
            threads[key].append([])

        convo = threads[key][-1] if threads[key] else []
        if direction == "in":
            convo.append((username, text))
        elif direction == "out":
            convo.append((avatar, text))

    return threads


def build_conversations_from_handled(
    handled: Dict,
) -> Dict[str, List[List[Tuple[str, str]]]]:
    """
    Build conversations from handled.json messages.

    Uses the stored message+reply pairs to reconstruct conversations.
    """
    threads: Dict[str, List[List[Tuple[str, str]]]] = {}
    messages = handled.get("messages", {})

    for msg_id, msg_data in messages.items():
        avatar = msg_data.get("avatar", "")
        sender = msg_data.get("sender", "unknown")
        incoming = msg_data.get("incoming", "")
        reply = msg_data.get("reply", "")

        if not incoming or not avatar:
            continue

        key = f"{avatar}:{sender}"
        if key not in threads:
            threads[key] = []

        # Find the most recent conversation with this sender
        found = False
        for convo in reversed(threads[key]):
            if convo and convo[-1][0] == sender:
                convo.append((avatar, reply))
                found = True
                break
        if not found:
            threads[key].append([(sender, incoming), (avatar, reply)])

    return threads


def generate_training_data(
    avatar_key: Optional[str] = None,
    max_conversations: int = 50,
) -> List[str]:
    """
    Generate persona training data from DM conversations.

    Args:
        avatar_key: If set, only generate for this avatar. None = all.
        max_conversations: Maximum conversations per avatar.

    Returns:
        List of training example strings in persona format.
    """
    personas = load_personas()
    interactions = load_interactions()
    handled = load_handled()

    # Build conversations from both sources
    convo_map: Dict[str, List[List[Tuple[str, str]]]] = {}

    # From interactions (preferred — has real conversation flow)
    if interactions:
        convo_map.update(build_conversations_from_interactions(interactions))

    # From handled (fallback — has stored message+reply pairs)
    if handled.get("messages"):
        handled_convos = build_conversations_from_handled(handled)
        for key, convos in handled_convos.items():
            if key not in convo_map:
                convo_map[key] = convos

    # Filter by avatar if specified
    if avatar_key:
        convo_map = {k: v for k, v in convo_map.items() if k.startswith(avatar_key + ":")}

    # Group by avatar
    avatar_convos: Dict[str, List[List[Tuple[str, str]]]] = {}
    for key, convos in convo_map.items():
        avatar = key.split(":")[0]
        if avatar not in avatar_convos:
            avatar_convos[avatar] = []
        avatar_convos[avatar].extend(convos[:max_conversations])

    # Format all conversations
    all_training = []
    for avatar, convos in avatar_convos.items():
        training = format_persona_training(avatar, convos, personas)
        all_training.extend(training)

    return all_training


def save_training_jsonl(training_data: List[str], output_path: Path) -> None:
    """Save training data as JSONL (one example per line)."""
    with open(output_path, 'w') as f:
        for example in training_data:
            f.write(json.dumps({"text": example}) + "\n")
    print(f"Saved {len(training_data)} examples to {output_path}")


def save_training_directory(training_data: List[str], output_dir: Path) -> None:
    """Save training data split by avatar."""
    output_dir.mkdir(parents=True, exist_ok=True)
    by_avatar: Dict[str, List[str]] = {}
    for example in training_data:
        # Extract avatar from <|persona|> marker
        import re
        match = re.search(r'<\|persona\|>(\w+)', example)
        if match:
            avatar = match.group(1)
            if avatar not in by_avatar:
                by_avatar[avatar] = []
            by_avatar[avatar].append(example)

    for avatar, examples in by_avatar.items():
        path = output_dir / f"{avatar}_training.jsonl"
        save_training_jsonl(examples, path)


def main():
    parser = argparse.ArgumentParser(description="Convert DM conversations to persona training data")
    parser.add_argument("--avatar", help="Generate for specific avatar only")
    parser.add_argument("--all", action="store_true", help="Generate for all avatars")
    parser.add_argument("--output", "-o", help="Output file path (JSONL)")
    parser.add_argument("--output-dir", help="Output directory (split by avatar)")
    parser.add_argument("--max-conv", type=int, default=50, help="Max conversations per avatar")
    parser.add_argument("--summary", action="store_true", help="Show summary without saving")

    args = parser.parse_args()

    if not args.avatar and not args.all and not args.output and not args.output_dir and not args.summary:
        parser.print_help()
        sys.exit(1)

    training = generate_training_data(
        avatar_key=args.avatar if not args.all else None,
        max_conversations=args.max_conv,
    )

    if args.summary:
        print(f"Total training examples: {len(training)}")
        # Count by avatar
        import re
        counts = {}
        for ex in training:
            match = re.search(r'<\|persona\|>(\w+)', ex)
            if match:
                avatar = match.group(1)
                counts[avatar] = counts.get(avatar, 0) + 1
        for avatar, count in sorted(counts.items()):
            print(f"  {avatar}: {count} examples")
        return

    if args.output:
        save_training_jsonl(training, Path(args.output))
    elif args.output_dir:
        save_training_directory(training, Path(args.output_dir))
    elif training:
        # Print to stdout
        for example in training:
            print(example)
            print("---")


if __name__ == "__main__":
    main()
