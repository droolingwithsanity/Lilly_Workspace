import logging
import random
from typing import Dict, List, Optional

from datasets import Dataset

from .persona_backgrounds import PERSONA_BACKGROUNDS, list_personas

logger = logging.getLogger("PersonaTrainer.DataPrep")


def format_conversation(
    utterances: List[str],
    persona_id: str,
    persona_info: dict,
    add_background: bool = True,
) -> str:
    bg = persona_info["background"]
    name = persona_info["name"]
    markers = persona_info["vocab_markers"]

    parts = []
    if add_background:
        parts.append(f"<|persona|>{persona_id}")
        parts.append(f"<|background|>{bg}")
        parts.append(f"<|style|>{persona_info['voice_style']}")
        parts.append(f"<|markers|>{', '.join(markers)}")
    parts.append("<|conversation|>")
    for i, text in enumerate(utterances):
        role = "user" if i % 2 == 0 else name
        if text.strip():
            parts.append(f"<|{role}|>{text.strip()}")
    parts.append(f"<|{name}|>")
    return "\n".join(parts)


def extract_conversations_personachat(example) -> List[List[str]]:
    """
    Persona-Chat has:
      personality: [str, ...]  — persona statements
      utterances: [{
        'history': [str, ...],   # alternating speaker/listener
        'candidates': [str, ...] # possible next responses
      }, ...]

    Builds full conversations: history + each candidate as a response.
    """
    convos = []
    utts = example.get("utterances", [])
    for ug in utts:
        history = ug.get("history", [])
        cands = ug.get("candidates", [])
        if not history:
            continue
        for cand in cands[:1]:  # use top candidate
            conversation = list(history)
            conversation.append(cand)
            # History should be odd length (user starts), append makes it even
            if len(conversation) >= 2:
                convos.append(conversation)
    return convos


def extract_conversations_dailydialog(example) -> List[List[str]]:
    """Extract from better_daily_dialog format (per-turn rows with dialog_id).

    The dataset has columns: dialog_id, utterance, turn_type, emotion.
    We don't use dialog grouping here — that's handled in prepare_dailydialog.
    """
    # Individual rows are handled by the grouped preparer
    return []


def extract_conversations_blended_skill_talk(example) -> List[List[str]]:
    convs = []
    free_messages = example.get("free_messages", [])
    if isinstance(free_messages, list) and len(free_messages) >= 2:
        convs.append(free_messages)
    guided_messages = example.get("guided_messages", [])
    if isinstance(guided_messages, list) and len(guided_messages) >= 2:
        convs.append(guided_messages)
    return convs


def extract_conversations_empathetic_dialogues(example) -> List[List[str]]:
    """Extract from empathetic_dialogues_for_lm format (pre-grouped conv lists)."""
    conv = example.get("conv", [])
    if isinstance(conv, list) and len(conv) >= 2:
        return [conv]
    return []


def prepare_personachat(
    dataset, persona_id: str, persona_info: dict, max_samples: Optional[int] = None
) -> List[str]:
    samples = []
    for i, ex in enumerate(dataset):
        if max_samples and len(samples) >= max_samples:
            break
        convos = extract_conversations_personachat(ex)
        personality = ex.get("personality", [])
        for convo in convos:
            samples.append(format_conversation(convo, persona_id, persona_info))
    return samples


def prepare_dailydialog(dataset, persona_id: str, persona_info: dict) -> List[str]:
    """Prepare from better_daily_dialog format.

    Dataset has per-turn rows with columns: dialog_id, utterance, turn_type, emotion.
    We group by dialog_id to reconstruct full conversations.
    """
    from collections import defaultdict

    # Group turns by dialog_id
    dialogs: Dict[str, List[str]] = defaultdict(list)
    for ex in dataset:
        dialog_id = str(ex.get("dialog_id", ""))
        utterance = str(ex.get("utterance", "")).strip()
        if utterance:
            dialogs[dialog_id].append(utterance)

    samples = []
    for dialog_id, turns in dialogs.items():
        if len(turns) >= 2:
            samples.append(format_conversation(turns, persona_id, persona_info))
    return samples


def prepare_blended_skill_talk(
    dataset, persona_id: str, persona_info: dict
) -> List[str]:
    samples = []
    for ex in dataset:
        convos = extract_conversations_blended_skill_talk(ex)
        for convo in convos:
            samples.append(format_conversation(convo, persona_id, persona_info))
    return samples


def prepare_empathetic_dialogues(
    dataset, persona_id: str, persona_info: dict
) -> List[str]:
    """Prepare from empathetic_dialogues_for_lm format.

    Each row has a 'conv' field which is already a list of utterances.
    """
    samples = []
    for ex in dataset:
        conv = ex.get("conv", [])
        if isinstance(conv, list) and len(conv) >= 2:
            # Clean up tokens like _comma_
            cleaned = [u.replace("_comma_", ",") for u in conv if u.strip()]
            if len(cleaned) >= 2:
                samples.append(format_conversation(cleaned, persona_id, persona_info))
    return samples


def prepare_cornell(dataset, persona_id: str, persona_info: dict) -> List[str]:
    """Prepare from mylesmharrison/cornell-movie-dialog format.

    Each row has a 'text' field formatted as "CHARACTER   dialogue\\r\\n".
    We parse into conversation pairs (consecutive speaker changes).
    """
    import re

    samples = []
    current_speaker = None
    current_lines: List[str] = []

    for ex in dataset:
        text = str(ex.get("text", "")).strip()
        if not text:
            continue

        # Parse "CHARACTER   dialogue" format
        match = re.match(r"^([A-Z_]+)\s{2,}(.+)$", text)
        if not match:
            continue

        speaker = match.group(1)
        dialogue = match.group(2).strip()

        if speaker != current_speaker:
            # Speaker changed — save previous speaker's combined text
            if current_lines:
                combined = " ".join(current_lines)
                current_lines = []
            else:
                combined = ""
            current_speaker = speaker
            if combined and dialogue:
                # We have a turn pair
                if len(samples) < 50000:  # cap to prevent huge dataset
                    samples.append(
                        format_conversation(
                            [combined, dialogue], persona_id, persona_info
                        )
                    )
        else:
            # Same speaker continues
            current_lines.append(dialogue)

    return samples


def prepare_fineweb(ds: Dataset, persona_id: str, persona_info: dict) -> List[str]:
    """FineWeb raw web text — persona-agnostic knowledge absorption.

    Not conversation-formatted on purpose: tagged <|knowledge|> so the model
    treats it as reference material, not dialogue to imitate. Same text for
    every persona (dedup keeps the dataset balanced).
    """
    if persona_id != list(PERSONA_BACKGROUNDS.keys())[0]:
        return []  # add once, not per-persona
    out = []
    for ex in ds:
        text = str(ex.get("text", "")).strip()
        if text:
            out.append(f"<|knowledge|>\n{text}")
    return out


DATASET_PREPARERS = {
    "persona_chat": prepare_personachat,
    "dailydialog": prepare_dailydialog,
    "blended_skill_talk": prepare_blended_skill_talk,
    "empathetic_dialogues": prepare_empathetic_dialogues,
    "cornell_movie": prepare_cornell,
    "fineweb": prepare_fineweb,
}


def prepare_training_data(
    raw_datasets: Dict[str, Dataset],
    personas_to_use: Optional[List[str]] = None,
    max_samples: Optional[int] = None,
) -> Dataset:
    if personas_to_use is None:
        personas_to_use = list_personas()
    logger.info(f"Preparing data for personas: {personas_to_use}")

    # Per-persona hard cap to prevent OOM
    per_persona_cap = None
    if max_samples and personas_to_use:
        per_persona_cap = max(10, max_samples // len(personas_to_use))

    all_texts: List[str] = []
    for persona_id in personas_to_use:
        p_info = PERSONA_BACKGROUNDS.get(persona_id)
        if not p_info:
            logger.warning(f"Unknown persona: {persona_id}, skipping")
            continue
        persona_texts: List[str] = []
        for ds_name, ds_data in raw_datasets.items():
            preparer = DATASET_PREPARERS.get(ds_name)
            if preparer and ds_data is not None:
                try:
                    samples = preparer(ds_data, persona_id, p_info)
                    if samples:
                        persona_texts.extend(samples)
                        logger.info(
                            f"  {persona_id} <- {ds_name}: {len(samples)} samples"
                        )
                except Exception as e:
                    logger.warning(f"  {persona_id} <- {ds_name}: error ({e})")
        # Hard cap per persona to keep memory bounded
        if per_persona_cap and len(persona_texts) > per_persona_cap:
            random.shuffle(persona_texts)
            persona_texts = persona_texts[:per_persona_cap]
        all_texts.extend(persona_texts)
        logger.info(
            f"  {persona_id}: total {len(persona_texts)} samples (cap={per_persona_cap})"
        )

    if not all_texts:
        logger.warning(
            "No training data from datasets! Creating synthetic fallback data."
        )
        all_texts = _create_fallback_data(personas_to_use)

    random.shuffle(all_texts)
    if max_samples and len(all_texts) > max_samples:
        all_texts = all_texts[:max_samples]

    logger.info(f"Total training samples: {len(all_texts)}")
    return Dataset.from_dict({"text": all_texts})


def _create_fallback_data(personas_to_use: List[str]) -> List[str]:
    convos = [
        [
            "Hey, how's it going?",
            "Ah, you know. Same old same old. What's on your mind?",
        ],
        [
            "What do you think about the weather lately?",
            "It's been something else, hasn't it? Makes you think about how small we really are.",
        ],
        [
            "I've been feeling kind of lost lately.",
            "Everybody feels that way sometimes. The trick is to keep moving. You don't have to have it all figured out.",
        ],
        [
            "Tell me something interesting.",
            "You know, most people think the opposite of standing out is fitting in. But really, the opposite of standing out is being invisible. And you, you're not invisible.",
        ],
        [
            "What's the best advice you've ever gotten?",
            "Someone once told me that the best time to plant a tree was twenty years ago. The second best time is now. That stuck with me.",
        ],
        [
            "Do you think people can change?",
            "Change is the only constant. But it takes work, and most people stop before the real change happens. The ones who push through? They're the ones who actually grow.",
        ],
        [
            "I'm working on a big project and I'm scared it'll fail.",
            "Failure's just data. Every miss tells you what doesn't work. The people who win are the ones who treat failure like feedback, not a verdict.",
        ],
        [
            "What makes you happy?",
            "The small things, mostly. A good conversation. The way light hits things at golden hour. When someone gets something I said without me having to explain it twice.",
        ],
    ]
    all_texts = []
    for pid in personas_to_use:
        p = PERSONA_BACKGROUNDS.get(pid)
        if not p:
            continue
        for convo in convos:
            all_texts.append(format_conversation(convo, pid, p))
    return all_texts
