"""Конфигурация: секреты из окружения, список таргетов из YAML.

Разделение намеренное. Токены не должны попадать в репозиторий, а список
сервисов — наоборот, должен: он меняется вместе с инфраструктурой, и его
история в git полезнее, чем ещё одна переменная окружения.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import yaml

from .probes.http import Endpoint


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_chat_id: str
    db_path: Path
    interval: timedelta
    failure_threshold: int
    recovery_threshold: int
    remind_after: timedelta
    railway_token: str | None
    railway_projects: list[str]
    railway_environment: str
    vercel_token: str | None
    vercel_projects: list[str]
    vercel_team_id: str | None
    endpoints: list[Endpoint] = field(default_factory=list)
    triage_enabled: bool = True

    @property
    def railway_enabled(self) -> bool:
        return bool(self.railway_token and self.railway_projects)

    @property
    def vercel_enabled(self) -> bool:
        return bool(self.vercel_token and self.vercel_projects)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Не задана обязательная переменная {name}")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом, а не «{raw}»") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_targets(path: Path) -> tuple[list[Endpoint], list[str], list[str]]:
    """Прочитать targets.yml: http-эндпоинты, проекты Railway и Vercel."""
    if not path.exists():
        return [], [], []

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    endpoints = []
    for item in data.get("http") or []:
        if "name" not in item or "url" not in item:
            raise ConfigError(f"В http-таргете нужны name и url: {item}")
        endpoints.append(
            Endpoint(
                name=item["name"],
                url=item["url"],
                expect=tuple(item.get("expect", (200, 201, 204))),
                timeout=float(item.get("timeout", 15.0)),
                expect_body=item.get("expect_body"),
            )
        )

    railway = list((data.get("railway") or {}).get("projects") or [])
    vercel = list((data.get("vercel") or {}).get("projects") or [])
    return endpoints, railway, vercel


def load(targets_path: Path | str = "targets.yml") -> Settings:
    endpoints, railway_projects, vercel_projects = load_targets(Path(targets_path))

    return Settings(
        telegram_token=_require("PULSE_TELEGRAM_TOKEN"),
        telegram_chat_id=_require("PULSE_TELEGRAM_CHAT_ID"),
        db_path=Path(os.environ.get("PULSE_DB_PATH", "./data/pulse.db")),
        interval=timedelta(seconds=_int("PULSE_INTERVAL_SECONDS", 180)),
        failure_threshold=_int("PULSE_FAILURE_THRESHOLD", 2),
        recovery_threshold=_int("PULSE_RECOVERY_THRESHOLD", 1),
        remind_after=timedelta(minutes=_int("PULSE_REMIND_AFTER_MINUTES", 120)),
        railway_token=os.environ.get("RAILWAY_TOKEN") or None,
        railway_projects=railway_projects,
        railway_environment=os.environ.get("PULSE_RAILWAY_ENVIRONMENT", "production"),
        vercel_token=os.environ.get("VERCEL_TOKEN") or None,
        vercel_projects=vercel_projects,
        vercel_team_id=os.environ.get("VERCEL_TEAM_ID") or None,
        endpoints=endpoints,
        triage_enabled=_bool("PULSE_TRIAGE_ENABLED", True),
    )
