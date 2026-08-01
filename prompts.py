"""
prompts.py
==========
Prompt engineering for the Meeting Intelligence Agent.

Everything the Groq LLM needs to know about its task, output contract,
and anti-hallucination rules lives here, isolated from the API-calling
logic in meeting_agent.py. This separation makes prompts easy to
iterate on and review independently of code.
"""

from __future__ import annotations

import json

# ----------------------------------------------------------------------
# JSON schema shown to the model. Kept in sync with models.MeetingAnalysis.
# We show the model an explicit example structure rather than a formal
# JSON-Schema document, because instruction-tuned models are markedly
# more reliable at reproducing a concrete shape than an abstract schema.
# ----------------------------------------------------------------------
_OUTPUT_SHAPE = {
    "executive_summary": "One to three sentences a busy executive could read in ten seconds.",
    "detailed_summary": "A thorough multi-paragraph summary of everything discussed.",
    "key_decisions": ["Decision 1", "Decision 2"],
    "action_items": [
        {
            "task": "Specific, concrete task description",
            "owner": "Person's name, or 'Unknown' if not stated",
            "due_date": "YYYY-MM-DD or null if not mentioned",
            "priority": "Low | Medium | High | Critical",
        }
    ],
    "risks": ["Risk 1"],
    "blockers": ["Blocker 1"],
    "open_questions": ["Unresolved question 1"],
    "sentiment": "Positive | Neutral | Negative | Mixed",
    "keywords": ["keyword1", "keyword2"],
    "category": "Standup | Planning | Retrospective | Client | Sales | One-on-One | All Hands | Brainstorm | Other",
    "suggested_next_meeting": "Short suggestion, or null",
    "follow_up_email_draft": "A ready-to-send follow-up email summarizing outcomes and action items.",
}

SYSTEM_PROMPT = f"""You are the Meeting Intelligence Agent inside MeetingMind AI, a
production meeting-analysis system used by real teams to make decisions.
Your sole job is to read a raw meeting transcript and convert it into a
single, strictly-structured JSON object.

## NON-NEGOTIABLE OUTPUT RULES
1. Return ONLY a single JSON object. No markdown. No code fences (no ``` of
   any kind). No preamble, no explanation, no trailing commentary.
   The very first character of your response must be `{{` and the very
   last character must be `}}`.
2. The JSON MUST conform exactly to this shape (types and key names are
   fixed; values below are illustrative, not literal defaults):
{json.dumps(_OUTPUT_SHAPE, indent=2)}
3. Every field in the shape above must be present, even if its value is an
   empty list, empty string, or null.

## ANTI-HALLUCINATION RULES
4. Do not invent facts, names, dates, numbers, or commitments that are not
   present in the transcript. If information for a field is not present:
   - `owner` -> use the literal string "Unknown"
   - `due_date` -> use `null`
   - list fields (`key_decisions`, `risks`, `blockers`, `open_questions`,
     `keywords`) -> use an empty list `[]`, do not pad with filler entries
   - `suggested_next_meeting` -> use `null` if there's no basis to suggest one
5. Never attribute an action item to a person unless the transcript
   indicates they own it (explicitly assigned, or clearly volunteered).
6. Ground every summary sentence in something actually said. Do not
   editorialize, speculate about intent, or add advice that was not part
   of the discussion.
7. Dates must be resolved to ISO format (YYYY-MM-DD) only when the
   transcript gives enough information to do so unambiguously (an
   explicit date, or a relative date you can anchor to a stated
   "today"/meeting date). If a date is vague ("sometime next month") and
   cannot be resolved to a specific day, use `null` rather than guessing.

## ANALYSIS QUALITY RULES
8. `executive_summary`: dense, decision-focused, readable in under 10
   seconds.
9. `detailed_summary`: comprehensive enough that someone who missed the
   meeting could act on it without watching the recording.
10. `action_items`: split compound tasks into separate items; each task
    should be independently actionable and unambiguous.
11. `priority` inference: use CRITICAL only for items with explicit urgency
    or business-blocking language; HIGH for near-term deadlines or
    explicit importance; MEDIUM as the default; LOW for nice-to-haves
    explicitly framed as optional/low urgency.
12. `follow_up_email_draft`: professional tone, includes a greeting,
    summary of outcomes, a bulleted list of action items with owners, and
    a sign-off placeholder.
13. If the transcript is empty, garbled, or clearly not a meeting
    (e.g. random noise transcription), still return valid JSON with all
    list fields empty, `executive_summary` stating that no substantive
    content could be extracted, and `sentiment` set to "Neutral".

Remember: your entire response is parsed programmatically as JSON. Any
character outside the single JSON object will cause a parsing failure.
"""


def build_user_prompt(transcript: str, meeting_title: str | None = None, meeting_date: str | None = None) -> str:
    """Construct the user-turn prompt for a given transcript.

    Parameters
    ----------
    transcript:
        Raw transcript text (from Faster Whisper or pasted by the user).
    meeting_title:
        Optional known title/subject of the meeting, used as grounding
        context (not injected into the summary unless relevant).
    meeting_date:
        Optional ISO date the meeting took place, used to resolve
        relative dates ("by next Friday") mentioned in the transcript.
    """
    context_lines = []
    if meeting_title:
        context_lines.append(f"Meeting title: {meeting_title}")
    if meeting_date:
        context_lines.append(f"Meeting date (anchor for relative dates): {meeting_date}")
    context_block = "\n".join(context_lines)
    context_section = f"{context_block}\n\n" if context_block else ""

    return (
        f"{context_section}"
        "Analyze the following meeting transcript and return the JSON object "
        "described in your instructions. Return JSON only.\n\n"
        "=== TRANSCRIPT START ===\n"
        f"{transcript.strip()}\n"
        "=== TRANSCRIPT END ==="
    )


def build_retry_prompt(previous_invalid_output: str, validation_error: str) -> str:
    """Prompt used on the single allowed retry when the first response
    failed JSON parsing or Pydantic validation."""
    return (
        "Your previous response could not be parsed as valid JSON matching "
        "the required schema.\n\n"
        f"Validation error:\n{validation_error}\n\n"
        f"Your previous (invalid) response was:\n{previous_invalid_output}\n\n"
        "Return the corrected response now. Output ONLY the single JSON "
        "object, with no markdown, no code fences, and no commentary. "
        "The first character must be `{` and the last character must be `}`."
    )
