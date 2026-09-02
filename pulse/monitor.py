"""Цикл проверок: опросить пробы, обновить состояние, разослать что нужно."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from . import notify
from .alerting import AlertPolicy
from .config import Settings
from .models import Action, CheckResult, utcnow
from .probes.base import Probe
from .probes.railway import RailwayClient
from .storage import Store
from .triage import analyze, format_triage

log = logging.getLogger(__name__)


class Monitor:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        probes: list[Probe],
        policy: AlertPolicy,
        bot: Bot,
        railway: RailwayClient | None = None,
    ):
        self._settings = settings
        self._store = store
        self._probes = probes
        self._policy = policy
        self._bot = bot
        self._railway = railway

    async def tick(self) -> list[CheckResult]:
        """Один полный прогон всех проб."""
        gathered = await asyncio.gather(
            *(probe.run() for probe in self._probes), return_exceptions=True
        )

        results: list[CheckResult] = []
        for probe, outcome in zip(self._probes, gathered):
            if isinstance(outcome, Exception):
                # Проба обязана ловить свои ошибки сама. Если не поймала —
                # это баг пробы, и он не должен останавливать остальные.
                log.exception("Проба %s упала: %s", probe.name, outcome)
                continue
            results.extend(outcome)

        for result in results:
            await self._handle(result)

        return results

    async def _handle(self, result: CheckResult) -> None:
        state = self._store.load(result.target)
        decision = self._policy.evaluate(state, result)
        self._store.save(decision.state)

        log.debug("%s: %s — %s", result.target, result.health.value, decision.reason)

        if decision.action is Action.NONE:
            return

        triage_text = None
        if decision.action is Action.ALERT:
            triage_text = await self._triage(result)
            self._store.open_incident(
                result.target, result.at, result.summary, triage_text
            )
        elif decision.action is Action.RECOVERED:
            self._store.close_incident(result.target, result.at)

        log.info("%s: %s", result.target, decision.action.value)

        await notify.send(
            self._bot,
            self._settings.telegram_chat_id,
            notify.render(decision.action, result, triage_text),
            notify.keyboard(
                result,
                can_restart=self._railway is not None
                and result.target.startswith("railway/"),
            )
            if decision.action is not Action.RECOVERED
            else None,
        )

    async def _triage(self, result: CheckResult) -> str | None:
        """Достать логи упавшего деплоя и попросить модель объяснить причину."""
        if not self._settings.triage_enabled or not result.ref:
            return None
        if self._railway is None or not result.target.startswith("railway/"):
            return None

        try:
            logs = await self._railway.logs(result.ref)
        except Exception as exc:
            log.warning("Логи %s не достались: %s", result.target, exc)
            return None

        # Клиент Anthropic синхронный, поэтому уводим его с event loop:
        # иначе на время разбора встают все остальные проверки.
        triage = await asyncio.to_thread(analyze, result.target, result.summary, logs)
        return format_triage(triage) if triage else None

    async def run_forever(self) -> None:
        interval = self._settings.interval.total_seconds()
        log.info(
            "Мониторинг запущен: %d проб, интервал %.0f с", len(self._probes), interval
        )
        while True:
            started = utcnow()
            try:
                results = await self.tick()
                log.info("Прогон завершён: %d таргетов", len(results))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Прогон упал целиком")

            elapsed = (utcnow() - started).total_seconds()
            await asyncio.sleep(max(1.0, interval - elapsed))
