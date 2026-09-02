#!/usr/bin/env python3
"""
Amazon Bedrock backend for Lilly AI.

Provides an OpenAI-compatible interface to Amazon Bedrock models
(e.g., Claude 3/4 via Anthropic, Llama via Meta, etc.).

Environment variables:
  AWS_ACCESS_KEY_ID     – AWS access key (optional if using IAM role / ~/.aws/credentials)
  AWS_SECRET_ACCESS_KEY – AWS secret key
  AWS_REGION            – AWS region (default: us-east-1)
  BEDROCK_MODEL         – default model ID (default: anthropic.claude-3-5-sonnet-20240620-v1:0)
"""

from __future__ import annotations

import json
import os
import time
import logging
from typing import Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# AWS Bedrock backend (optional)
BOTO3_AVAILABLE: bool = False
boto3 = None  # type: ignore
try:
    import boto3 as _boto3  # type: ignore

    boto3 = _boto3
    BOTO3_AVAILABLE = True
except ImportError:
    pass


class BedrockBackend:
    """Amazon Bedrock LLM backend with OpenAI-compatible chat interface."""

    def __init__(self) -> None:
        self.model_name = os.environ.get(
            "BEDROCK_MODEL", "anthropic.claude-3-5-sonnet-20240620-v1:0"
        )
        self.region = os.environ.get("AWS_REGION", "us-east-1")
        self._client = None
        self._response_cache: dict[str, dict] = {}
        self._cache_ttl: float = 300.0
        self._cache_max: int = 200

    def _get_client(self):
        if self._client is None:
            if not BOTO3_AVAILABLE:
                raise RuntimeError("boto3 not installed. Run: pip install boto3")
            if boto3 is None:
                raise RuntimeError("boto3 import failed")
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            )
        return self._client

    def _cache_key(
        self, messages: list[dict], temperature: float, max_tokens: int
    ) -> str:
        import hashlib, json as _json

        data = _json.dumps(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            sort_keys=True,
        )
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Convert OpenAI-style messages to Bedrock/system prompt format."""
        system_prompt = ""
        filtered = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_prompt += content + "\n"
            else:
                filtered.append({"role": role, "content": content})
        return system_prompt.strip(), filtered

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 80,
        timeout: int = 120,
        model: str = "",
    ) -> str:
        use_model = model or self.model_name

        # Check cache
        try:
            ck = self._cache_key(messages, temperature, max_tokens)
            cached = self._response_cache.get(ck)
            if cached and (time.time() - cached["ts"]) < self._cache_ttl:
                return cached["text"]
        except Exception:
            pass

        system_prompt, bedrock_messages = self._convert_messages(messages)

        # Build request body based on model provider
        if "anthropic" in use_model:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": bedrock_messages,
            }
        elif "llama" in use_model.lower() or "meta" in use_model.lower():
            body = {
                "prompt": self._build_llama_prompt(messages),
                "max_gen_len": max_tokens,
                "temperature": temperature,
            }
        else:
            # Generic fallback
            body = {
                "prompt": self._build_llama_prompt(messages),
                "max_gen_len": max_tokens,
                "temperature": temperature,
            }

        try:
            client = self._get_client()
            response = client.invoke_model(
                modelId=use_model,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())

            # Parse response based on model type
            if "anthropic" in use_model:
                text = result.get("content", [{}])[0].get("text", "")
            elif "llama" in use_model.lower() or "meta" in use_model.lower():
                text = result.get("generation", "")
            else:
                text = result.get("generation", result.get("text", ""))

            text = (text or "").strip()

            # Cache result
            try:
                ck = self._cache_key(messages, temperature, max_tokens)
                self._response_cache[ck] = {"text": text, "ts": time.time()}
                if len(self._response_cache) > self._cache_max:
                    now = time.time()
                    self._response_cache = {
                        k: v
                        for k, v in self._response_cache.items()
                        if now - v["ts"] < self._cache_ttl
                    }
            except Exception:
                pass

            return text
        except Exception as e:
            raise RuntimeError(f"Bedrock request failed: {e}")

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 80,
        model: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream chat response token by token from Bedrock."""
        use_model = model or self.model_name
        system_prompt, bedrock_messages = self._convert_messages(messages)

        if "anthropic" in use_model:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": bedrock_messages,
            }
        else:
            body = {
                "prompt": self._build_llama_prompt(messages),
                "max_gen_len": max_tokens,
                "temperature": temperature,
            }

        try:
            client = self._get_client()
            response = client.invoke_model_with_response_stream(
                modelId=use_model,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            stream = response.get("body")
            if not stream:
                return
            for event in stream:
                chunk = event.get("chunk")
                if not chunk:
                    continue
                try:
                    data = json.loads(chunk.get("bytes", b"{}").decode())
                    if "anthropic" in use_model:
                        delta = data.get("delta", {}).get("text", "")
                    else:
                        delta = data.get("generation", "")
                    if delta:
                        yield delta
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Bedrock stream error: {e}")
            return

    def _build_llama_prompt(self, messages: list[dict]) -> str:
        """Build a Llama-style prompt from messages."""
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                parts.append(f"<|system|>\n{content}\n")
            elif role == "user":
                parts.append(f"<|user|>\n{content}\n")
            elif role == "assistant":
                parts.append(f"<|assistant|>\n{content}\n")
        parts.append("<|assistant|>\n")
        return "".join(parts)

    def is_available(self) -> bool:
        """Check if Bedrock backend is configured and reachable."""
        if not BOTO3_AVAILABLE:
            return False
        try:
            client = self._get_client()
            # Quick health check: list foundation models
            resp = client.list_foundation_models(maxResults=1)
            return True
        except Exception:
            return False
