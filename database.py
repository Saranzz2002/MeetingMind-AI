"""
database.py
===========
Supabase-backed persistence layer for meeting records.

Expected table (create via Supabase SQL editor before first run):

```sql
create table if not exists meetings (
    id text primary key,
    user_email text not null default 'guest',
    title text not null default 'Untitled Meeting',
    source_filename text,
    transcript text not null,
    analysis jsonb not null,
    created_at timestamptz not null default now()
);
create index if not exists meetings_user_email_idx on meetings (user_email);
create index if not exists meetings_created_at_idx on meetings (created_at desc);
```

All methods raise `DatabaseError` on failure so callers (app.py) can
show a clean error message instead of leaking raw Supabase exceptions.
"""

from __future__ import annotations

from typing import Optional

from config import get_supabase_client, logger
from models import MeetingRecord

TABLE_NAME = "meetings"


class DatabaseError(Exception):
    """Raised when a Supabase operation fails."""


class Database:
    """Thin, explicit CRUD wrapper around the Supabase Python client.

    Kept framework-free and dependency-injectable (accepts an optional
    client) so it's straightforward to unit test with a fake client.
    """

    def __init__(self, client=None) -> None:
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = get_supabase_client()
        return self._client

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def insert_meeting(self, record: MeetingRecord) -> MeetingRecord:
        try:
            self.client.table(TABLE_NAME).insert(record.to_supabase_row()).execute()
            logger.info("Inserted meeting %s for user %s.", record.id, record.user_email)
            return record
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to insert meeting %s.", record.id)
            raise DatabaseError(f"Could not save meeting to database: {exc}") from exc

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get_history(self, user_email: str, limit: int = 50) -> list[MeetingRecord]:
        try:
            response = (
                self.client.table(TABLE_NAME)
                .select("*")
                .eq("user_email", user_email)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return [MeetingRecord.from_supabase_row(row) for row in response.data]
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch history for %s.", user_email)
            raise DatabaseError(f"Could not fetch meeting history: {exc}") from exc

    def get_meeting(self, meeting_id: str) -> Optional[MeetingRecord]:
        try:
            response = (
                self.client.table(TABLE_NAME)
                .select("*")
                .eq("id", meeting_id)
                .limit(1)
                .execute()
            )
            if not response.data:
                return None
            return MeetingRecord.from_supabase_row(response.data[0])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch meeting %s.", meeting_id)
            raise DatabaseError(f"Could not fetch meeting: {exc}") from exc

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def delete_meeting(self, meeting_id: str, user_email: str) -> bool:
        """Delete a meeting, scoped to its owning user_email so one user
        cannot delete another user's records."""
        try:
            response = (
                self.client.table(TABLE_NAME)
                .delete()
                .eq("id", meeting_id)
                .eq("user_email", user_email)
                .execute()
            )
            deleted = bool(response.data)
            if deleted:
                logger.info("Deleted meeting %s for user %s.", meeting_id, user_email)
            return deleted
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to delete meeting %s.", meeting_id)
            raise DatabaseError(f"Could not delete meeting: {exc}") from exc

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search_meetings(self, user_email: str, query: str, limit: int = 50) -> list[MeetingRecord]:
        """Case-insensitive substring search across title and transcript."""
        try:
            response = (
                self.client.table(TABLE_NAME)
                .select("*")
                .eq("user_email", user_email)
                .or_(f"title.ilike.%{query}%,transcript.ilike.%{query}%")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return [MeetingRecord.from_supabase_row(row) for row in response.data]
        except Exception as exc:  # noqa: BLE001
            logger.exception("Search failed for user %s, query=%r.", user_email, query)
            raise DatabaseError(f"Could not search meetings: {exc}") from exc
