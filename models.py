"""
models.py
=========
Pydantic data models used across MeetingMind AI.

These models serve three purposes:
1. Define the strict JSON schema the LLM must produce (meeting_agent.py
   validates the raw Groq response against `MeetingAnalysis`).
2. Provide validated, typed objects the rest of the application
   (database.py, app.py, utils.py) can rely on instead of raw dicts.
3. Normalize messy/missing LLM output into safe defaults ("Unknown"
   owner, `None` due dates, clamped priorities) via field validators.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class Priority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class Sentiment(str, Enum):
    POSITIVE = "Positive"
    NEUTRAL = "Neutral"
    NEGATIVE = "Negative"
    MIXED = "Mixed"


class MeetingCategory(str, Enum):
    STANDUP = "Standup"
    PLANNING = "Planning"
    RETROSPECTIVE = "Retrospective"
    CLIENT = "Client"
    SALES = "Sales"
    ONE_ON_ONE = "One-on-One"
    ALL_HANDS = "All Hands"
    BRAINSTORM = "Brainstorm"
    OTHER = "Other"


class ActionItem(BaseModel):
    """A single actionable task extracted from the transcript."""

    task: str = Field(..., min_length=1, description="What needs to be done.")
    owner: str = Field(default="Unknown", description="Person responsible.")
    due_date: Optional[date] = Field(
        default=None, description="ISO date the task is due, if mentioned."
    )
    priority: Priority = Field(default=Priority.MEDIUM)

    @field_validator("owner", mode="before")
    @classmethod
    def default_owner(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return "Unknown"
        return v

    @field_validator("due_date", mode="before")
    @classmethod
    def blank_due_date_to_none(cls, v):
        if v in ("", "null", "None", None, "TBD", "N/A"):
            return None
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority(cls, v):
        if v is None:
            return Priority.MEDIUM
        if isinstance(v, str):
            candidate = v.strip().capitalize()
            if candidate in Priority._value2member_map_:
                return candidate
        return Priority.MEDIUM


class MeetingAnalysis(BaseModel):
    """The full structured output produced by the Meeting Intelligence
    Agent (meeting_agent.py) for one transcript. This is the contract
    the LLM's JSON output must satisfy."""

    executive_summary: str = Field(..., min_length=1)
    detailed_summary: str = Field(..., min_length=1)
    key_decisions: List[str] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    sentiment: Sentiment = Field(default=Sentiment.NEUTRAL)
    keywords: List[str] = Field(default_factory=list)
    category: MeetingCategory = Field(default=MeetingCategory.OTHER)
    suggested_next_meeting: Optional[str] = None
    follow_up_email_draft: str = Field(default="")

    @field_validator("sentiment", mode="before")
    @classmethod
    def normalize_sentiment(cls, v):
        if isinstance(v, str):
            candidate = v.strip().capitalize()
            if candidate in Sentiment._value2member_map_:
                return candidate
        return Sentiment.NEUTRAL

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, v):
        if isinstance(v, str):
            for member in MeetingCategory:
                if member.value.lower() == v.strip().lower():
                    return member.value
        return MeetingCategory.OTHER


class MeetingRecord(BaseModel):
    """A full persisted meeting: raw transcript + structured analysis +
    metadata. This is the shape stored in / read from Supabase."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_email: str = Field(default="guest")
    title: str = Field(default="Untitled Meeting")
    source_filename: Optional[str] = None
    transcript: str
    analysis: MeetingAnalysis
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def to_supabase_row(self) -> dict:
        """Flatten to a JSON-serializable dict suitable for a Supabase
        insert (nested Pydantic models -> plain dict/JSON)."""
        return {
            "id": self.id,
            "user_email": self.user_email,
            "title": self.title,
            "source_filename": self.source_filename,
            "transcript": self.transcript,
            "analysis": self.analysis.model_dump(mode="json"),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_supabase_row(cls, row: dict) -> "MeetingRecord":
        return cls(
            id=row["id"],
            user_email=row.get("user_email", "guest"),
            title=row.get("title", "Untitled Meeting"),
            source_filename=row.get("source_filename"),
            transcript=row.get("transcript", ""),
            analysis=MeetingAnalysis.model_validate(row["analysis"]),
            created_at=row.get("created_at", datetime.utcnow()),
        )
