"""LLM wrapper for generating semi() function implementations."""
from __future__ import annotations

from typing import Optional

from semipy.config import get_config


class SemiGenerator:
    """OpenAI API wrapper for generating Python functions from semantic prompts."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        config = get_config()
        self.model = model or config.model
        self._api_key = api_key or config.api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError("openai package is required; install with: pip install openai")
            if not self._api_key:
                raise ValueError("OPENAI_API_KEY must be set (env or semi.configure(api_key=...))")
            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=False,
            reasoning_effort="minimal",
        )
        return response.choices[0].message.content or ""


SYSTEM_PROMPT = """You generate a single Python function that implements the user's semantic request.

Rules:
- Output only one function. No explanations, no markdown outside the code block.
- Wrap the function in a ```python code block.
- The function must be pure Python: no external API calls, no imports beyond standard library if needed.
- Parameters: the user prompt may reference "the value" or "this row" or similar; those become the first parameter(s). Other fixed context (sample data, condition strings) are described in the prompt; bake them into the function or add parameters as needed.
- Return type: match exactly what the user needs (bool for conditions, str for text, int/float for numbers, or the described type). Return that type only.
- Handle edge cases: None, missing keys, empty data, type mismatches. Prefer safe defaults over raising.
- Be generalizable: the function may be used on other similar data. Avoid hardcoding values that were only in the example; use the described intent.
- Do not use emoji or decorative output.
"""
