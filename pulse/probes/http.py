"""Проба живости по HTTP: то, что видит пользователь, а не панель провайдера.

Deploy со статусом SUCCESS и приложение, отвечающее 502, — разные вещи.
Поэтому HTTP-проба нужна даже там, где уже есть Railway или Vercel.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from ..models import CheckResult, Health

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str
    #: Коды, считающиеся здоровьем. 401/403 бывают нормой для закрытого API.
    expect: tuple[int, ...] = (200, 201, 204)
    timeout: float = 15.0
    #: Подстрока, которая должна встретиться в теле ответа.
    expect_body: str | None = None


class HttpProbe:
    name = "http"

    def __init__(self, endpoints: list[Endpoint], *, slow_ms: int = 3000):
        self._endpoints = endpoints
        self._slow_ms = slow_ms

    async def run(self) -> list[CheckResult]:
        if not self._endpoints:
            return []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return list(
                await asyncio.gather(
                    *(self._check(client, ep) for ep in self._endpoints)
                )
            )

    async def _check(self, client: httpx.AsyncClient, ep: Endpoint) -> CheckResult:
        started = time.perf_counter()
        try:
            response = await client.get(ep.url, timeout=ep.timeout)
        except httpx.TimeoutException:
            return CheckResult(
                target=ep.name,
                health=Health.FAIL,
                summary=f"таймаут за {ep.timeout:.0f} с",
                detail=ep.url,
            )
        except httpx.HTTPError as exc:
            # Сетевая ошибка на нашей стороне неотличима от лежащего сервиса,
            # и трактуется как отказ: пользователь увидел бы то же самое.
            return CheckResult(
                target=ep.name,
                health=Health.FAIL,
                summary=f"сеть недоступна: {type(exc).__name__}",
                detail=str(exc),
            )

        latency = int((time.perf_counter() - started) * 1000)

        if response.status_code not in ep.expect:
            return CheckResult(
                target=ep.name,
                health=Health.FAIL,
                summary=f"HTTP {response.status_code}",
                detail=response.text[:500],
                latency_ms=latency,
            )

        if ep.expect_body and ep.expect_body not in response.text:
            return CheckResult(
                target=ep.name,
                health=Health.FAIL,
                summary=f"HTTP {response.status_code}, но в теле нет «{ep.expect_body}»",
                detail=response.text[:500],
                latency_ms=latency,
            )

        note = f"HTTP {response.status_code} за {latency} мс"
        if latency > self._slow_ms:
            note += " (медленно)"
        return CheckResult(
            target=ep.name, health=Health.OK, summary=note, latency_ms=latency
        )
