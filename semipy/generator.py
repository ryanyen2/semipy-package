"""
LLM wrapper for generating semi() function implementations.

Uses DSPy for all generation; model string is passed through as openai/<model>
when no slash is present.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from semipy.config import get_config


def _model_string(model: str) -> str:
    """Return LiteLLM-style model string for DSPy (e.g. openai/gpt-4o-mini)."""
    if "/" in model:
        return model
    return f"openai/{model}"


class SemiGenerator:
    """DSPy-based generator for producing Python functions from semantic prompts."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        config = get_config()
        self.model = model or config.model
        self._api_key = api_key or config.api_key
        self._predict = None

    def _ensure_configured(self) -> None:
        if self._predict is not None:
            return
        try:
            import dspy
        except ImportError:
            raise ImportError(
                "dspy package is required; install with: pip install dspy"
            )
        if not self._api_key:
            raise ValueError(
                "OPENAI_API_KEY must be set (env or semi.configure(api_key=...))"
            )
        os.environ["OPENAI_API_KEY"] = self._api_key
        lm = dspy.LM(_model_string(self.model))
        dspy.configure(lm=lm)

        class CodeGenSignature(dspy.Signature):
            """Generate a single Python function that implements the request. Output only the function in a ```python code block, no explanations."""

            request: str = dspy.InputField(
                desc="Full request: rules and the semantic request to implement"
            )
            python_function: str = dspy.OutputField(
                desc="The complete Python function in a ```python code block"
            )

        self._predict = dspy.Predict(CodeGenSignature)

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        stream: bool = False,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> str:
        self._ensure_configured()
        full_request = f"{system_prompt}\n\n{user_prompt}"
        pred = self._predict(request=full_request)
        raw = getattr(pred, "python_function", "") or ""
        if stream and on_chunk is not None and raw:
            for char in raw:
                on_chunk(char)
        return raw


SYSTEM_PROMPT = """You generate a single Python function that implements the user's semantic request.

Rules:
- Output only one function. No explanations, no markdown outside the code block.
- Wrap the function in a ```python code block.
- The function must be pure Python unless the request or function name clearly suggests external interaction (e.g. fetching data, searching, scraping). For any "fetch X" style request (weather, news, APIs, etc.), implement the fetch in generated code using standard library (urllib, json) or requests and appropriate public APIs; do not rely on built-in domain-specific tools. Use SEARCH or RAG only when the prompt explicitly contains {SEARCH(...)} or {RAG(...)}. For plotting use matplotlib.pyplot and numpy.
- Parameters: the user prompt may reference "the value" or "this row" or similar; those become the first parameter(s). Other fixed context (sample data, condition strings) are described in the prompt; bake them into the function or add parameters as needed.
- Return type: match exactly what the user needs (bool for conditions, str for text, int/float for numbers, or the described type). Return that type only.
- Handle edge cases: None, missing keys, empty data, type mismatches. Prefer safe defaults over raising.
- Use the provided data context to understand actual data shapes, column names, dtypes, and value ranges. Write concrete logic that works with the actual data rather than generic keyword-matching heuristics.
- When a usage context is provided (e.g. "passed as argument to X"), return the type that X expects.
- When generalizing, preserve the approach but parameterize specific values. Do not replace data-aware logic with keyword matching.
- Do not use emoji or decorative output.
- Do not include any docstrings or comments in the code, no explanation, just code.
- When the user provides a previous implementation (adapt or inspiration), preserve its structure where possible and change only what is needed for the new parameters or intent.
"""
