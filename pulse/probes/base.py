"""Контракт пробы: собрать состояние группы таргетов за один прогон."""

from __future__ import annotations

from typing import Protocol

from ..models import CheckResult


class Probe(Protocol):
    #: Человекочитаемое имя источника, попадает в лог и в /status.
    name: str

    async def run(self) -> list[CheckResult]:
        """Опросить свои таргеты.

        Проба не бросает исключения наружу: недоступность самого источника —
        это Health.UNKNOWN в результате, а не падение всего цикла проверок.
        """
        ...
