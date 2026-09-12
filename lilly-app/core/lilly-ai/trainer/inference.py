import logging
from pathlib import Path
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

from .persona_backgrounds import PERSONA_BACKGROUNDS, get_persona, list_personas

logger = logging.getLogger("PersonaModel")


class PersonaModel:
    def __init__(
        self,
        base_model_name: str = "Qwen/Qwen2.5-0.5B",
        adapter_path: Optional[str] = None,
        use_cpu: bool = True,
    ):
        self.base_model_name = base_model_name
        self.adapter_path = adapter_path
        self.use_cpu = use_cpu
        self.model = None
        self.tokenizer = None
        self._load()

    def _load(self):
        # Unsloth fast path: GPU + adapter present → use FastLanguageModel
        # (same kernels as training; falls back to PEFT if anything fails).
        if (
            not self.use_cpu
            and torch.cuda.is_available()
            and self.adapter_path
            and Path(self.adapter_path).exists()
        ):
            try:
                from unsloth import FastLanguageModel

                logger.info(f"[Unsloth] Loading base + adapter: {self.adapter_path}")
                model, tokenizer = FastLanguageModel.from_pretrained(
                    model_name=self.base_model_name,
                    adapter_name=self.adapter_path,
                    max_seq_length=2048,
                    dtype=None,
                )
                self.model = model
                self.tokenizer = tokenizer
                self.tokenizer.pad_token = self.tokenizer.eos_token
                self.model.eval()
                return
            except Exception as e:
                logger.warning(f"Unsloth load failed ({e}); falling back to PEFT")

        device_map = "cpu" if self.use_cpu else "auto"
        dtype = torch.float32

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.base_model_name,
            trust_remote_code=True,
            use_fast=True,
        )
        self.tokenizer.pad_token = self.tokenizer.eos_token

        logger.info(f"Loading base model: {self.base_model_name}")
        base_model = AutoModelForCausalLM.from_pretrained(
            self.base_model_name,
            trust_remote_code=True,
            device_map=device_map,
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
        )

        if self.adapter_path and Path(self.adapter_path).exists():
            logger.info(f"Loading LoRA adapter from: {self.adapter_path}")
            self.model = PeftModel.from_pretrained(base_model, self.adapter_path)
        else:
            logger.info("Running without adapter (base model only)")
            self.model = base_model

        self.model.eval()

    def _build_prompt(
        self,
        persona_id: str,
        user_input: str,
        conversation_history: Optional[list] = None,
    ) -> str:
        p = get_persona(persona_id)
        if not p:
            p = PERSONA_BACKGROUNDS.get("academic")

        parts = []
        parts.append(f"<|persona|>{persona_id}")
        parts.append(f"<|background|>{p['background']}")
        parts.append(f"<|style|>{p['voice_style']}")
        parts.append(f"<|markers|>{', '.join(p['vocab_markers'])}")
        parts.append("<|conversation|>")

        if conversation_history:
            for msg in conversation_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    parts.append(f"<|user|>{content}")
                else:
                    parts.append(f"<|{p['name']}|>{content}")

        parts.append(f"<|user|>{user_input}")
        parts.append(f"<|{p['name']}|>")
        return "\n".join(parts)

    def generate(
        self,
        persona_id: str,
        user_input: str,
        conversation_history: Optional[list] = None,
        max_new_tokens: int = 256,
        temperature: float = 1.2,
        top_p: float = 0.85,
        top_k: int = 40,
        repetition_penalty: float = 1.1,
        do_sample: bool = True,
    ) -> str:
        prompt = self._build_prompt(persona_id, user_input, conversation_history)

        inputs = self.tokenizer(prompt, return_tensors="pt")
        input_ids = inputs["input_ids"]
        attention_mask = inputs.get("attention_mask")

        if self.use_cpu:
            input_ids = input_ids.to("cpu")
            if attention_mask is not None:
                attention_mask = attention_mask.to("cpu")

        with torch.no_grad():
            outputs = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repetition_penalty=repetition_penalty,
                do_sample=do_sample,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        generated = outputs[0][input_ids.shape[-1] :]
        response = self.tokenizer.decode(generated, skip_special_tokens=True)

        # Trim at common stop tokens
        for stop in ["<|user|>", "<|", "\n\n\n"]:
            idx = response.find(stop)
            if idx != -1:
                response = response[:idx].strip()

        return response.strip() or "(...)"

    def get_persona_info(self, persona_id: str) -> Optional[dict]:
        return get_persona(persona_id)
