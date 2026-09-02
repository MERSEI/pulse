"""Точка входа: поднимает цикл проверок и Telegram-бота в одном процессе."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from aiogram import Bot

from . import bot as bot_module
from .alerting import AlertPolicy
from .config import ConfigError, Settings, load, load_dotenv
from .monitor import Monitor
from .probes.base import Probe
from .probes.http import HttpProbe
from .probes.railway import RailwayClient, RailwayProbe
from .probes.vercel import VercelProbe
from .storage import Store

log = logging.getLogger("pulse")


def build_probes(settings: Settings, railway: RailwayClient | None) -> list[Probe]:
    probes: list[Probe] = []

    if settings.endpoints:
        probes.append(HttpProbe(settings.endpoints))

    if railway is not None:
        probes.append(
            RailwayProbe(
                railway,
                settings.railway_projects,
                environment=settings.railway_environment,
            )
        )

    if settings.vercel_enabled:
        probes.append(
            VercelProbe(
                settings.vercel_token,
                settings.vercel_projects,
                team_id=settings.vercel_team_id,
            )
        )

    return probes


async def run(settings: Settings, once: bool) -> int:
    store = Store(settings.db_path)
    railway = RailwayClient(settings.railway_token) if settings.railway_enabled else None
    probes = build_probes(settings, railway)

    if not probes:
        log.error("Нечего проверять: заполните targets.yml и токены в окружении")
        return 1

    policy = AlertPolicy(
        failure_threshold=settings.failure_threshold,
        recovery_threshold=settings.recovery_threshold,
        remind_after=settings.remind_after,
    )

    if once:
        # Диагностика конфигурации: ни бота, ни токена Telegram не требуется —
        # иначе первую проверку «а вижу ли я вообще свои сервисы» нельзя было
        # бы сделать, не заведя сначала бота.
        monitor = Monitor(settings, store, probes, policy, None, railway)
        icon = {"ok": "OK  ", "fail": "FAIL", "unknown": "??  "}
        for result in await monitor.tick():
            mark = icon.get(result.health.value, "    ")
            print(f"{mark} {result.target}: {result.summary}")
        return 0

    if not settings.telegram_enabled:
        log.error(
            "Для постоянного режима нужны PULSE_TELEGRAM_TOKEN и "
            "PULSE_TELEGRAM_CHAT_ID. Разовый прогон доступен: pulse --once"
        )
        return 2

    bot = Bot(token=settings.telegram_token)
    monitor = Monitor(settings, store, probes, policy, bot, railway)

    try:
        dp = bot_module.build_dispatcher(settings, store, railway)
        # Падение любой из двух задач должно валить процесс целиком: watchdog,
        # у которого молча умер цикл проверок и остался жив бот, — худший
        # вариант из возможных, он выглядит работающим.
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(monitor.run_forever()),
                asyncio.create_task(bot_module.start_polling(bot, dp)),
            ],
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()  # пробрасываем исключение, если оно было
        return 0
    finally:
        await bot.session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pulse", description="Watchdog для сервисов на Railway и Vercel."
    )
    parser.add_argument(
        "--once", action="store_true", help="один прогон проверок и выход"
    )
    parser.add_argument("--targets", default="targets.yml", help="путь к targets.yml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # httpx логирует каждый запрос на INFO. При десятке таргетов раз в три
    # минуты это единственное, что будет в логе, и в нём утонут собственные
    # сообщения о падениях — ради которых лог и читают.
    if not args.verbose:
        for noisy in ("httpx", "httpcore", "aiogram.event"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    load_dotenv()

    try:
        settings = load(args.targets)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(run(settings, args.once))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
