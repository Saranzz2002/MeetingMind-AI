"""
meeting_agent.py
================
The Meeting Intelligence Agent: turns a raw transcript into a validated
`MeetingAnalysis` object using Groq's hosted `llama-3.3-70b-versatile`.

Reasoning / control flow
-------------------------
1. Send transcript + system prompt to Groq, requesting JSON-only output.
2. Strip any incidental wrapping (code fences) defensively, even though
   the prompt forbids them, since LLMs occasionally ignore formatting
   instructions.
3. Parse as JSON, validate against `MeetingAnalysis` (Pydantic).
4. On ANY failure (bad JSON, schema violation, empty response), retry
   exactly once with an explicit error-correction prompt that includes
   the previous output and the validation error.
5. If the retry also fails, raise `MeetingAnalysisError` so the caller
   (app.py) can surface a clear error to the user instead of silently
   returning garbage.

No agent framework (LangChain/CrewAI/etc.) is used -- this is a direct,
explicit call to the Groq SDK, which keeps the control flow auditable
and removes an entire class of framework-induced failure modes.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import ValidationError

from config import get_groq_client, logger, settings
from models import MeetingAnalysis
from prompts import SYSTEM_PROMPT, build_retry_prompt, build_user_prompt

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class MeetingAnalysisError(Exception):
    """Raised when the agent cannot produce a valid MeetingAnalysis
    after its retry budget is exhausted."""


class MeetingAgent:
    """Encapsulates all Groq calls related to meeting intelligence."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.2) -> None:
        self.model = model or settings.groq_model
        self.temperature = temperature

    # ------------------------------------------------------------------
    # Low-level Groq call
    # ------------------------------------------------------------------
    def _call_groq(self, system_prompt: str, user_prompt: str) -> str:
        client = get_groq_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Groq API call failed.")
            raise MeetingAnalysisError(f"Groq API request failed: {exc}") from exc

        if not response.choices:
            raise MeetingAnalysisError("Groq returned no choices in the response.")

        content = response.choices[0].message.content
        if not content or not content.strip():
            raise MeetingAnalysisError("Groq returned an empty response.")
        return content.strip()

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_code_fences(raw: str) -> str:
        """Defensively remove markdown code fences if the model added
        them despite instructions not to."""
        cleaned = _CODE_FENCE_RE.sub("", raw).strip()
        return cleaned

    @staticmethod
    def _extract_json_object(raw: str) -> str:
        """Extract the outermost {...} JSON object from raw text, in
        case the model prepended/appended stray text."""
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise MeetingAnalysisError("No JSON object found in model output.")
        return raw[start : end + 1]

    def _parse_and_validate(self, raw: str) -> MeetingAnalysis:
        cleaned = self._strip_code_fences(raw)
        json_str = self._extract_json_object(cleaned)
        data = json.loads(json_str)  # may raise json.JSONDecodeError
        return MeetingAnalysis.model_validate(data)  # may raise ValidationError

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def analyze(
        self,
        transcript: str,
        meeting_title: Optional[str] = None,
        meeting_date: Optional[str] = None,
    ) -> MeetingAnalysis:
        """Run the full transcript -> structured analysis pipeline.

        Raises
        ------
        MeetingAnalysisError
            If the transcript is empty, or if both the initial attempt
            and the single retry fail to produce valid output.
        """
        if not transcript or not transcript.strip():
            raise MeetingAnalysisError("Cannot analyze an empty transcript.")

        user_prompt = build_user_prompt(transcript, meeting_title, meeting_date)

        # --- Attempt 1 ---
        raw_output = self._call_groq(SYSTEM_PROMPT, user_prompt)
        try:
            analysis = self._parse_and_validate(raw_output)
            logger.info("Meeting analysis succeeded on first attempt.")
            return analysis
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("First analysis attempt failed validation: %s", exc)
            first_error = str(exc)
            first_raw = raw_output

        # --- Attempt 2 (single retry, per spec) ---
        retry_prompt = build_retry_prompt(first_raw, first_error)
        raw_output_retry = self._call_groq(SYSTEM_PROMPT, retry_prompt)
        try:
            analysis = self._parse_and_validate(raw_output_retry)
            logger.info("Meeting analysis succeeded on retry.")
            return analysis
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("Retry analysis attempt also failed validation: %s", exc)
            raise MeetingAnalysisError(
                "The AI model failed to produce valid structured output after "
                f"one retry. Last error: {exc}"
            ) from exc
