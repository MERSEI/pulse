"""Типы, общие для проб, состояния и уведомлений."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Health(str, Enum):
    OK = "ok"
    FAIL = "fail"
    #: Проба не смогла ответить на вопрос — сеть, токен, лимит API.
    #: Это не падение сервиса, и алертить как падение нельзя.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CheckResult:
    """Результат одной пробы одного таргета."""

    target: str
    health: Health
    summary: str
    detail: str = ""
    latency_ms: int | None = None
    #: Идентификатор, по которому можно достать логи (deployment id и т.п.)
    ref: str | None = None
    at: datetime = field(default_factory=utcnow)

    @property
    def ok(self) -> bool:
        return self.health is Health.OK


@dataclass
class TargetState:
    """Что мы помним о таргете между прогонами."""

    target: str
    health: Health = Health.UNKNOWN
    consecutive_fails: int = 0
    consecutive_oks: int = 0
    #: Открыт ли сейчас инцидент, о котором уже сообщили.
    alerted: bool = False
    last_alert_at: datetime | None = None
    muted_until: datetime | None = None

    def is_muted(self, now: datetime | None = None) -> bool:
        if self.muted_until is None:
            return False
        return (now or utcnow()) < self.muted_until


class Action(str, Enum):
    NONE = "none"
    ALERT = "alert"
    REMIND = "remind"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class Decision:
    """Что делать с результатом проверки."""

    action: Action
    state: TargetState
    reason: str = ""
