"""
calendar_service.py
====================
Read-only Google Calendar integration. Lists today's and upcoming
meetings for the authenticated user. Never creates, modifies, or
deletes calendar events -- enforced by only ever calling the
`events().list(...)` endpoint with `calendar.readonly` scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import logger


class CalendarError(Exception):
    """Raised when the Calendar API cannot be reached or parsed."""


@dataclass
class CalendarEvent:
    id: str
    summary: str
    start: Optional[datetime]
    end: Optional[datetime]
    attendees: list[str]
    html_link: Optional[str]


class CalendarService:
    """Wraps the Google Calendar API v3 (read-only)."""

    def __init__(self, credentials) -> None:
        """
        Parameters
        ----------
        credentials:
            A `google.oauth2.credentials.Credentials` object obtained
            from `auth.GoogleAuthenticator`. Guest-mode users have no
            credentials, so this service should not be constructed for
            them (callers should check `AuthenticatedUser.is_guest`).
        """
        if credentials is None:
            raise CalendarError("Cannot access Calendar without Google credentials.")
        self._credentials = credentials
        self._service = None

    def _get_service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build("calendar", "v3", credentials=self._credentials)
        return self._service

    def _list_events(self, time_min: datetime, time_max: datetime, max_results: int) -> list[CalendarEvent]:
        service = self._get_service()
        try:
            response = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Google Calendar API request failed.")
            raise CalendarError(f"Could not read calendar: {exc}") from exc

        events: list[CalendarEvent] = []
        for item in response.get("items", []):
            start_raw = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
            end_raw = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
            events.append(
                CalendarEvent(
                    id=item.get("id", ""),
                    summary=item.get("summary", "(No title)"),
                    start=_parse_dt(start_raw),
                    end=_parse_dt(end_raw),
                    attendees=[a.get("email", "") for a in item.get("attendees", [])],
                    html_link=item.get("htmlLink"),
                )
            )
        return events

    def get_todays_meetings(self) -> list[CalendarEvent]:
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        return self._list_events(start_of_day, end_of_day, max_results=25)

    def get_upcoming_meetings(self, days_ahead: int = 7) -> list[CalendarEvent]:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)
        return self._list_events(now, end, max_results=25)


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        # Handles both date-only ("2026-08-01") and full RFC3339 timestamps.
        if len(raw) == 10:
            return datetime.fromisoformat(raw)
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
