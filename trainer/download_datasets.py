import logging
from pathlib import Path
from typing import Dict, List, Optional

from datasets import load_dataset, concatenate_datasets, DatasetDict, Dataset

logger = logging.getLogger("PersonaTrainer.Data")


def download_dailydialog_pq() -> Optional[Dataset]:
    """DailyDialog as parquet (no loading script needed)."""
    logger.info("Downloading DailyDialog (parquet)...")
    try:
        ds = load_dataset(
            "parquet",
            data_files={
                "train": "https://huggingface.co/datasets/li2017dailydialog/daily_dialog/resolve/main/data/train-00000-of-00001.parquet",
                "validation": "https://huggingface.co/datasets/li2017dailydialog/daily_dialog/resolve/main/data/validation-00000-of-00001.parquet",
                "test": "https://huggingface.co/datasets/li2017dailydialog/daily_dialog/resolve/main/data/test-00000-of-00001.parquet",
            },
            split="train",
        )
        return ds
    except Exception as e:
        logger.debug(f"DailyDialog parquet failed: {e}")
        return None


def download_persona_chat(split: str = "train") -> Optional[Dataset]:
    logger.info("Downloading Persona-Chat...")
    try:
        ds = load_dataset("AlekseyKorshuk/persona-chat", split=split)
        return ds
    except Exception as e:
        logger.warning(f"Persona-Chat failed: {e}")
    return None


def download_cornell_movie() -> Optional[Dataset]:
    logger.info("Downloading Cornell Movie Dialogs (JSONL)...")
    try:
        ds = load_dataset(
            "json",
            data_files="https://huggingface.co/datasets/ivanzidov/cornell_movie_dialogues/resolve/main/data/train.jsonl",
            split="train",
        )
        return ds
    except Exception as e:
        logger.debug(f"Cornell JSONL failed: {e}")
        return None


def download_blended_skill_talk() -> Optional[Dataset]:
    """Blended Skill Talk — free, multi-persona conversations."""
    logger.info("Downloading Blended Skill Talk...")
    try:
        ds = load_dataset("blended_skill_talk", split="train")
        return ds
    except Exception as e:
        logger.debug(f"BST failed: {e}")
        return None


def download_empathetic_dialogues() -> Optional[Dataset]:
    """Empathetic Dialogues — emotional conversations."""
    logger.info("Downloading Empathetic Dialogues...")
    try:
        ds = load_dataset("empathetic_dialogues", split="train")
        return ds
    except Exception as e:
        logger.debug(f"ED failed: {e}")
        return None


def download_all_datasets(cache_dir: str = "dataset_cache") -> Dict[str, Dataset]:
    result = {}
    datasets_to_try = {
        "persona_chat": lambda: download_persona_chat("train"),
        "dailydialog": download_dailydialog_pq,
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
