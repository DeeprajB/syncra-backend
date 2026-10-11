import secrets
import time
from typing import Dict, Optional, Tuple

from app.core.config import settings


class TicketStore:
    def __init__(self) -> None:
        self._tickets: Dict[str, Tuple[str, str, float]] = {}

    def issue(self, user_id: str, workspace_id: str) -> Tuple[str, int]:
        self._purge(time.monotonic())
        token = secrets.token_urlsafe(32)
        ttl = settings.ws_ticket_ttl_seconds
        self._tickets[token] = (user_id, workspace_id, time.monotonic() + ttl)
        return token, ttl

    def redeem(self, token: str, workspace_id: str) -> Optional[str]:
        now = time.monotonic()
        self._purge(now)
        record = self._tickets.get(token)
        if record is None:
            return None
        user_id, bound_workspace, expires_at = record
        if expires_at < now:
            self._tickets.pop(token, None)
            return None
        if bound_workspace != workspace_id:
            return None
        self._tickets.pop(token, None)
        return user_id

    def reset(self) -> None:
        self._tickets.clear()

    def _purge(self, now: float) -> None:
        expired = [token for token, record in self._tickets.items() if record[2] < now]
        for token in expired:
            self._tickets.pop(token, None)


tickets = TicketStore()
