"""Session state machine and registry."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, Optional


class SessionStatus(Enum):
    PENDING = auto()
    COERCING = auto()
    CAPTURED = auto()
    RELAYING = auto()
    RELAY_SUCCESS = auto()
    POST_AUTH = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass
class RelaySession:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: SessionStatus = SessionStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # Source of the incoming NTLM auth
    source_ip: str = ""
    source_port: int = 0
    listener_protocol: str = ""

    # Coercion metadata
    coerce_target: str = ""
    coerce_method: str = ""

    # NTLM identity (populated by relay engine)
    domain: str = ""
    username: str = ""
    nthash: Optional[str] = None
    lmhash: Optional[str] = None

    # Relay target
    relay_target: Optional[str] = None
    relay_protocol: Optional[str] = None

    # Post-auth results
    post_auth_results: list[dict] = field(default_factory=list)
    error: Optional[str] = None

    def transition(self, new_status: SessionStatus, **kwargs: Any) -> None:
        self.status = new_status
        self.updated_at = datetime.utcnow()
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.name,
            "source": f"{self.source_ip}:{self.source_port}",
            "identity": f"{self.domain}\\{self.username}" if self.domain or self.username else "",
            "relay_target": self.relay_target,
            "coerce": f"{self.coerce_method}@{self.coerce_target}",
            "created": self.created_at.isoformat(),
            "updated": self.updated_at.isoformat(),
            "error": self.error,
            "results": self.post_auth_results,
        }


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: Dict[str, RelaySession] = {}

    def create(self, **kwargs: Any) -> RelaySession:
        sess = RelaySession(**kwargs)
        self._sessions[sess.id] = sess
        return sess

    def get(self, sid: str) -> Optional[RelaySession]:
        return self._sessions.get(sid)

    def by_status(self, status: SessionStatus) -> list[RelaySession]:
        return [s for s in self._sessions.values() if s.status == status]

    def transition(self, sid: str, status: SessionStatus, **kwargs: Any) -> Optional[RelaySession]:
        sess = self.get(sid)
        if sess:
            sess.transition(status, **kwargs)
        return sess

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for s in self._sessions.values():
            counts[s.status.name] = counts.get(s.status.name, 0) + 1
        return counts
