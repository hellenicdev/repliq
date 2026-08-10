"""LLM-based phrase segmentation (Groq).

Turns "I don't know what you're talking about" into
["I don't know", "what you're talking about"] so the search layer can look
for each natural phrase instead of raw words.

Degrades gracefully: if no GROQ_API_KEY is configured or the API call
fails, the sentence is split on punctuation, then passed as-is.
"""

import json
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = (
    "You segment user sentences into short natural spoken phrases suitable for "
    "searching movie dialogue databases. Phrases should be 1-6 words, complete "
    "speech fragments, and together cover the whole sentence without repeating words."
)


async def segment_sentence(sentence: str) -> list[str]:
    if not settings.groq_api_key:
        return _fallback_segments(sentence)

    try:
        payload = {
            "model": settings.groq_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f'Segment this sentence into phrases. Return ONLY a JSON array of strings, e.g. ["phrase one", "phrase two"].\n\nSentence: "{sentence}"'},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json=payload,
            )
        if resp.status_code != 200:
            logger.warning("groq error %s: %s", resp.status_code, resp.text[:300])
            return _fallback_segments(sentence)

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            for value in parsed.values():
                if isinstance(value, list):
                    parsed = value
                    break
        phrases = [str(p).strip() for p in parsed if str(p).strip()]
        if phrases:
            return phrases
    except Exception as exc:  # noqa: BLE001 - never break generation because of the LLM
        logger.warning("groq segmentation failed, using fallback: %s", exc)

    return _fallback_segments(sentence)


def _fallback_segments(sentence: str) -> list[str]:
    import re

    parts = [p.strip() for p in re.split(r"[,;.!?]+|\s+and\s+", sentence) if p.strip()]
    return parts or [sentence.strip()]
