"""Проба Vercel через REST API.

Интересует последний production-деплой каждого проекта: состояние ERROR
означает, что выкладка не доехала и на домене осталась прошлая версия —
снаружи это незаметно, HTTP-проба такое не поймает.
"""

from __future__ import annotations

import logging

import httpx

from ..models import CheckResult, Health

log = logging.getLogger(__name__)

API = "https://api.vercel.com"

#: readyState последнего деплоя.
BAD_STATES = {"ERROR", "CANCELED"}
TRANSIENT_STATES = {"BUILDING", "INITIALIZING", "QUEUED"}


class VercelError(RuntimeError):
    pass


class VercelProbe:
    name = "vercel"

    def __init__(
        self,
        token: str,
        project_ids: list[str],
        *,
        team_id: str | None = None,
        timeout: float = 30.0,
    ):
        self._token = token
        self._project_ids = project_ids
        self._team_id = team_id
        self._timeout = timeout

    async def run(self) -> list[CheckResult]:
        if not self._project_ids:
            return []

        results: list[CheckResult] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for project_id in self._project_ids:
                try:
                    results.append(await self._check(client, project_id))
                except (VercelError, httpx.HTTPError) as exc:
                    log.warning("Vercel %s недоступен: %s", project_id, exc)
                    results.append(
                        CheckResult(
                            target=f"vercel/{project_id}",
                            health=Health.UNKNOWN,
                            summary=f"API Vercel не ответил: {exc}",
                        )
                    )
        return results

    async def _check(self, client: httpx.AsyncClient, project_id: str) -> CheckResult:
        params = {"projectId": project_id, "limit": 1, "target": "production"}
        if self._team_id:
            params["teamId"] = self._team_id

        response = await client.get(
            f"{API}/v6/deployments",
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if response.status_code >= 400:
            raise VercelError(f"HTTP {response.status_code}: {response.text[:200]}")

        deployments = response.json().get("deployments") or []
        target = f"vercel/{project_id}"

        if not deployments:
            return CheckResult(
                target=target,
                health=Health.UNKNOWN,
                summary="production-деплоев ещё не было",
            )

        deployment = deployments[0]
        state = deployment.get("readyState") or deployment.get("state") or "UNKNOWN"

        if state in BAD_STATES:
            health = Health.FAIL
        elif state == "READY":
            health = Health.OK
        elif state in TRANSIENT_STATES:
            health = Health.UNKNOWN
        else:
            health = Health.UNKNOWN

        return CheckResult(
            target=target,
            health=health,
            summary=f"деплой {state}",
            detail=deployment.get("url") or "",
            ref=deployment.get("uid") or deployment.get("id"),
        )
