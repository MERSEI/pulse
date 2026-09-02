import httpx
import pytest

from pulse.models import Health
from pulse.probes.http import Endpoint, HttpProbe


def probe_with(handler, endpoints):
    """HttpProbe, у которого AsyncClient подменён на MockTransport."""
    probe = HttpProbe(endpoints)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return [await probe._check(client, ep) for ep in endpoints]

    return run


@pytest.mark.asyncio
async def test_expected_status_is_healthy():
    handler = lambda request: httpx.Response(200, text="ok")
    run = probe_with(handler, [Endpoint(name="api", url="https://x.test/health")])
    (result,) = await run()
    assert result.health is Health.OK
    assert result.latency_ms is not None


@pytest.mark.asyncio
async def test_unexpected_status_fails():
    handler = lambda request: httpx.Response(502, text="bad gateway")
    run = probe_with(handler, [Endpoint(name="api", url="https://x.test/")])
    (result,) = await run()
    assert result.health is Health.FAIL
    assert "502" in result.summary


@pytest.mark.asyncio
async def test_custom_expected_codes_are_respected():
    # Закрытый API отдаёт 401 и это норма.
    handler = lambda request: httpx.Response(401)
    run = probe_with(
        handler, [Endpoint(name="api", url="https://x.test/", expect=(401,))]
    )
    (result,) = await run()
    assert result.health is Health.OK


@pytest.mark.asyncio
async def test_body_assertion_catches_a_broken_page():
    # Статус 200, но отрисовалась страница ошибки — HTTP-код такое не ловит.
    handler = lambda request: httpx.Response(200, text="<h1>Application error</h1>")
    run = probe_with(
        handler,
        [Endpoint(name="site", url="https://x.test/", expect_body="Добро пожаловать")],
    )
    (result,) = await run()
    assert result.health is Health.FAIL
    assert "нет" in result.summary


@pytest.mark.asyncio
async def test_timeout_is_a_failure_not_an_exception():
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    run = probe_with(handler, [Endpoint(name="api", url="https://x.test/")])
    (result,) = await run()
    assert result.health is Health.FAIL
    assert "таймаут" in result.summary


@pytest.mark.asyncio
async def test_connection_error_is_a_failure():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    run = probe_with(handler, [Endpoint(name="api", url="https://x.test/")])
    (result,) = await run()
    assert result.health is Health.FAIL


@pytest.mark.asyncio
async def test_empty_endpoint_list_does_nothing():
    assert await HttpProbe([]).run() == []
