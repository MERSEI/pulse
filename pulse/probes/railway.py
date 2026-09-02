"""Проба Railway через публичный GraphQL API.

Endpoint и запросы взяты из docs.railway.com/integrations/api. Токен нужен
аккаунтный или workspace: проектный ходит по другому заголовку и видит только
своё окружение.
"""

from __future__ import annotations

import logging

import httpx

from ..models import CheckResult, Health

log = logging.getLogger(__name__)

ENDPOINT = "https://backboard.railway.com/graphql/v2"

#: Статусы, при которых сервис считается упавшим.
BAD_STATUSES = {"FAILED", "CRASHED"}
#: Промежуточные статусы: деплой едет, судить рано.
TRANSIENT_STATUSES = {"BUILDING", "DEPLOYING", "QUEUED", "WAITING", "INITIALIZING"}

PROJECT_QUERY = """
query project($id: String!) {
  project(id: $id) {
    id
    name
    services { edges { node { id name } } }
    environments { edges { node { id name } } }
  }
}
"""

DEPLOYMENTS_QUERY = """
query deployments($input: DeploymentListInput!) {
  deployments(input: $input, first: 1) {
    edges { node { id status createdAt staticUrl } }
  }
}
"""

LOGS_QUERY = """
query deploymentLogs($deploymentId: String!, $limit: Int) {
  deploymentLogs(deploymentId: $deploymentId, limit: $limit) {
    timestamp
    message
    severity
  }
}
"""


class RailwayError(RuntimeError):
    pass


class RailwayClient:
    def __init__(self, token: str, *, timeout: float = 30.0):
        self._token = token
        self._timeout = timeout

    async def query(self, query: str, variables: dict) -> dict:
        """Выполнить GraphQL-запрос.

        Railway отдаёт HTTP 200 с массивом errors даже при отказе авторизации,
        поэтому проверять только код ответа недостаточно.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables},
            )

        if response.status_code == 429:
            raise RailwayError(
                f"лимит запросов, повтор через {response.headers.get('Retry-After', '?')} с"
            )
        if response.status_code >= 400:
            raise RailwayError(f"HTTP {response.status_code}: {response.text[:200]}")

        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(e.get("message", "?") for e in payload["errors"])
            raise RailwayError(messages)

        return payload.get("data") or {}

    async def logs(self, deployment_id: str, limit: int = 200) -> list[str]:
        data = await self.query(
            LOGS_QUERY, {"deploymentId": deployment_id, "limit": limit}
        )
        return [entry["message"] for entry in data.get("deploymentLogs") or []]


class RailwayProbe:
    """Смотрит статус последнего деплоя каждого сервиса в проекте."""

    name = "railway"

    def __init__(
        self,
        client: RailwayClient,
        project_ids: list[str],
        *,
        environment: str = "production",
    ):
        self._client = client
        self._project_ids = project_ids
        self._environment = environment

    async def run(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for project_id in self._project_ids:
            try:
                results.extend(await self._check_project(project_id))
            except RailwayError as exc:
                log.warning("Railway %s недоступен: %s", project_id, exc)
                results.append(
                    CheckResult(
                        target=f"railway/{project_id}",
                        health=Health.UNKNOWN,
                        summary=f"API Railway не ответил: {exc}",
                    )
                )
            except httpx.HTTPError as exc:
                results.append(
                    CheckResult(
                        target=f"railway/{project_id}",
                        health=Health.UNKNOWN,
                        summary=f"сеть до Railway недоступна: {exc}",
                    )
                )
        return results

    async def _check_project(self, project_id: str) -> list[CheckResult]:
        data = await self._client.query(PROJECT_QUERY, {"id": project_id})
        project = data.get("project")
        if not project:
            raise RailwayError(f"проект {project_id} не найден")

        env_id = self._environment_id(project)
        if env_id is None:
            raise RailwayError(
                f"окружение «{self._environment}» не найдено в проекте {project['name']}"
            )

        results = []
        for edge in project["services"]["edges"]:
            service = edge["node"]
            results.append(
                await self._check_service(project, service, env_id)
            )
        return results

    def _environment_id(self, project: dict) -> str | None:
        for edge in project["environments"]["edges"]:
            if edge["node"]["name"] == self._environment:
                return edge["node"]["id"]
        return None

    async def _check_service(self, project: dict, service: dict, env_id: str) -> CheckResult:
        target = f"railway/{project['name']}/{service['name']}"
        data = await self._client.query(
            DEPLOYMENTS_QUERY,
            {
                "input": {
                    "projectId": project["id"],
                    "serviceId": service["id"],
                    "environmentId": env_id,
                }
            },
        )

        edges = (data.get("deployments") or {}).get("edges") or []
        if not edges:
            return CheckResult(
                target=target, health=Health.UNKNOWN, summary="деплоев ещё не было"
            )

        deployment = edges[0]["node"]
        status = deployment["status"]

        if status in BAD_STATUSES:
            health = Health.FAIL
        elif status in TRANSIENT_STATUSES:
            # Деплой в процессе — это не отказ и не здоровье.
            health = Health.UNKNOWN
        elif status == "SUCCESS":
            health = Health.OK
        else:
            # SLEEPING, REMOVED, SKIPPED — состояния, о которых судить не нам.
            health = Health.UNKNOWN

        return CheckResult(
            target=target,
            health=health,
            summary=f"деплой {status}",
            detail=deployment.get("staticUrl") or "",
            ref=deployment["id"],
        )
