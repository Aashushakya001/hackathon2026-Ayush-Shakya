"""
agent/llm_client.py — Azure OpenAI client wrapper.
Single point of truth for all LLM calls. Handles:
  - Structured JSON responses
  - System + user prompt construction
  - Token usage logging
  - Error handling
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import AzureOpenAI
from config import config

logger = logging.getLogger(__name__)

_client: Optional[AzureOpenAI] = None


def get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            api_key=config.AZURE_OPENAI_API_KEY,
            azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
            api_version=config.AZURE_OPENAI_API_VERSION,
        )
    return _client


async def llm_call(
    system_prompt: str,
    user_prompt: str,
    expect_json: bool = True,
    temperature: float = 0.1,
    max_tokens: int = 1500,
) -> dict | str:
    """
    Makes a single LLM call to Azure OpenAI GPT-4o-mini.
    Returns parsed JSON dict if expect_json=True, else raw string.
    """
    import asyncio
    client = get_client()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    loop = asyncio.get_event_loop()

    def _sync_call():
        return client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT_NAME,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"} if expect_json else None,
        )

    response = await loop.run_in_executor(None, _sync_call)

    content = response.choices[0].message.content.strip()
    usage = response.usage

    logger.debug(
        f"[LLM] tokens: prompt={usage.prompt_tokens}, "
        f"completion={usage.completion_tokens}"
    )

    if expect_json:
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"[LLM] JSON parse failed: {e}\nContent: {content[:300]}")
            # Try to extract JSON from markdown code block
            import re
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"LLM returned invalid JSON: {content[:200]}")

    return content
