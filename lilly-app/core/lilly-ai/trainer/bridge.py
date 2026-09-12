import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, AsyncIterator
import asyncio

from .inference import PersonaModel
from .persona_backgrounds import PERSONA_BACKGROUNDS, get_persona, list_personas

logger = logging.getLogger("PersonaBridge")

PERSONA_MAP = {
    "companion": "academic",
    "sage": "craftsman",
    "rebel": "street_kid",
    "oracle": "jazz_musician",
    "scientist": "academic",
}


class PersonaLLMBackend:
    def __init__(
        self,
        base_model: str = "Qwen/Qwen2.5-0.5B",
        adapter_path: Optional[str] = None,
        use_cpu: bool = True,
    ):
        self.base_model = base_model
        self.adapter_path = adapter_path
        self.use_cpu = use_cpu
        self._model: Optional[PersonaModel] = None

    async def load(self):
        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(
            None,
            lambda: PersonaModel(
                base_model_name=self.base_model,
                adapter_path=self.adapter_path,
                use_cpu=self.use_cpu,
            ),
        )
        logger.info("PersonaLLMBackend loaded")

    def is_loaded(self) -> bool:
        return self._model is not None

    def map_persona(self, entity_persona_id: str) -> str:
        return PERSONA_MAP.get(entity_persona_id, "academic")

    def get_persona_background(self, entity_persona_id: str) -> dict:
        trainer_id = self.map_persona(entity_persona_id)
        p = get_persona(trainer_id)
        if p:
            return {
                "trainer_id": trainer_id,
                "name": p["name"],
                "background": p["background"],
                "voice_style": p["voice_style"],
                "vocab_markers": p["vocab_markers"],
            }
        return {}

    async def generate(
        self,
        persona_id: str,
        user_input: str,
        conversation_history: Optional[List[dict]] = None,
        temperature: float = 1.2,
    ) -> str:
        if not self._model:
            return "(model not loaded)"

        trainer_id = self.map_persona(persona_id)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._model.generate(
                persona_id=trainer_id,
                user_input=user_input,
                conversation_history=conversation_history,
                temperature=temperature,
            ),
        )
        return response

    async def generate_stream(
        self,
        persona_id: str,
        user_input: str,
        conversation_history: Optional[List[dict]] = None,
        temperature: float = 1.2,
    ) -> AsyncIterator[str]:
        response = await self.generate(
            persona_id, user_input, conversation_history, temperature
        )
        for chunk in self._chunk_text(response):
            yield chunk

    def _chunk_text(self, text: str, chunk_size: int = 4):
        words = text.split()
        for i in range(0, len(words), chunk_size):
            yield " ".join(words[i : i + chunk_size]) + " "

    def list_available_personas(self) -> List[str]:
        return list_personas()
