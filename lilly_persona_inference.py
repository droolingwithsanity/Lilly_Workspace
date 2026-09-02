"""Runtime LoRA inference for Lilly persona models.

Uses transformers + PEFT to load a trained LoRA adapter alongside a base model
without requiring Ollama. This is used as a fallback when the Ollama GGUF
persona model cannot be loaded, or when richer persona behavior is needed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel, PeftConfig

WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", "/home/labhrasd/Lilly_Workspace"))
ADAPTER_DIR = WORKSPACE / "trained_persona_model" / "final"
BASE_MODEL = os.environ.get("PERSONA_BASE_MODEL", "Qwen/Qwen2.5-0.5B")
DEVICE = "cpu"  # keep CPU-only to avoid CUDA driver issues on this host

_persona_model = None
_persona_tokenizer = None
_persona_loaded = False
_persona_error: Optional[str] = None


def _load_persona():
    global _persona_model, _persona_tokenizer, _persona_loaded, _persona_error
    if _persona_loaded:
        return _persona_model is not None
    if not ADAPTER_DIR.exists():
        _persona_error = f"adapter dir missing: {ADAPTER_DIR}"
        _persona_loaded = True
        return False
    try:
        _persona_tokenizer = AutoTokenizer.from_pretrained(
            str(ADAPTER_DIR), trust_remote_code=True
        )
        _persona_model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL,
            torch_dtype=torch.float32,
            device_map="cpu",
            trust_remote_code=True,
        )
        _persona_model = PeftModel.from_pretrained(_persona_model, str(ADAPTER_DIR))
        _persona_model = _persona_model.merge_and_unload()
        _persona_model.eval()
        _persona_loaded = True
        _persona_error = None
        return True
    except Exception as exc:  # pragma: no cover
        _persona_error = str(exc)
        _persona_model = None
        _persona_tokenizer = None
        _persona_loaded = True
        return False


def is_available() -> bool:
    return _load_persona()


def last_error() -> Optional[str]:
    return _persona_error


def generate(
    prompt: str,
    *,
    max_new_tokens: int = 256,
    temperature: float = 0.7,
    top_p: float = 0.9,
) -> str:
    if not _load_persona():
        raise RuntimeError(_persona_error or "persona model not loaded")
    if _persona_model is None or _persona_tokenizer is None:
        raise RuntimeError("persona model not initialized")
    inputs = _persona_tokenizer(prompt, return_tensors="pt")
    with torch.no_grad():
        out = _persona_model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            do_sample=temperature > 0,
            pad_token_id=_persona_tokenizer.eos_token_id,
        )
    text = _persona_tokenizer.decode(out[0], skip_special_tokens=True)
    if text.startswith(prompt):
        text = text[len(prompt) :].lstrip()
    return text.strip()


def persona_chat(messages: List[Dict[str, str]]) -> str:
    """Convert chat messages to a prompt and generate a persona response."""
    if not _load_persona():
        raise RuntimeError(_persona_error or "persona model not loaded")
    if _persona_tokenizer is None or _persona_model is None:
        raise RuntimeError("persona model not initialized")
    prompt = ""
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            prompt += f"System: {content}\n"
        elif role == "user":
            prompt += f"User: {content}\n"
        elif role == "assistant":
            prompt += f"Assistant: {content}\n"
    prompt += "Assistant:"
    return generate(prompt)
