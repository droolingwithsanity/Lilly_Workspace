import logging
from pathlib import Path
from typing import Dict, List, Optional

from datasets import load_dataset, concatenate_datasets, DatasetDict, Dataset

logger = logging.getLogger("PersonaTrainer.Data")


def download_persona_chat(split: str = "train") -> Optional[Dataset]:
    """Persona-Chat — multi-turn conversations with persona statements."""
    logger.info("Downloading Persona-Chat...")
    try:
        ds = load_dataset("AlekseyKorshuk/persona-chat", split=split)
        return ds
    except Exception as e:
        logger.warning(f"Persona-Chat failed: {e}")
    return None


def download_blended_skill_talk() -> Optional[Dataset]:
    """Blended Skill Talk — free + guided multi-persona conversations."""
    logger.info("Downloading Blended Skill Talk...")
    try:
        ds = load_dataset("ParlAI/blended_skill_talk", split="train")
        return ds
    except Exception as e:
        logger.debug(f"BST failed: {e}")
        return None


def download_dailydialog() -> Optional[Dataset]:
    """DailyDialog — turn-level dialog with emotion labels (alternative source).

    Original li2017dailydialog/daily_dialog uses a deprecated loading script.
    This mirror has the same data in Parquet-compatible format.
    """
    logger.info("Downloading DailyDialog (pixelsandpointers)...")
    try:
        ds = load_dataset("pixelsandpointers/better_daily_dialog", split="train")
        return ds
    except Exception as e:
        logger.debug(f"DailyDialog failed: {e}")
        return None


def download_empathetic_dialogues() -> Optional[Dataset]:
    """Empathetic Dialogues — emotional conversations (alternative source).

    Original facebook/empathetic_dialogues uses a deprecated loading script.
    This mirror pre-groups conversations into lists.
    """
    logger.info("Downloading Empathetic Dialogues (pixelsandpointers)...")
    try:
        ds = load_dataset(
            "pixelsandpointers/empathetic_dialogues_for_lm", split="train"
        )
        return ds
    except Exception as e:
        logger.debug(f"ED failed: {e}")
        return None


def download_cornell_movie() -> Optional[Dataset]:
    """Cornell Movie Dialogs — movie character conversations.

    Original ivanzidov/cornell_movie_dialogues is private/401.
    This mirror has the same data with lines formatted as "CHARACTER   dialogue".
    """
    logger.info("Downloading Cornell Movie Dialogs...")
    try:
        ds = load_dataset("mylesmharrison/cornell-movie-dialog", split="train")
        return ds
    except Exception as e:
        logger.debug(f"Cornell failed: {e}")
        return None


def download_all_datasets(cache_dir: str = "dataset_cache") -> Dict[str, Dataset]:
    """Download all available datasets, skipping any that fail.

    Returns a dict mapping dataset names to HuggingFace Dataset objects.
    Each dataset is downloaded independently — failures don't block others.
    """
    result = {}
    datasets_to_try = {
        "persona_chat": lambda: download_persona_chat("train"),
        "dailydialog": download_dailydialog,
        "blended_skill_talk": download_blended_skill_talk,
        "empathetic_dialogues": download_empathetic_dialogues,
        "cornell_movie": download_cornell_movie,
    }
    for name, func in datasets_to_try.items():
        try:
            data = func()
            if data is not None:
                logger.info(f"  {name}: {len(data)} examples")
                result[name] = data
            else:
                logger.warning(f"  {name}: no data returned")
        except Exception as e:
            logger.warning(f"  {name}: skipped ({e})")
    return result
