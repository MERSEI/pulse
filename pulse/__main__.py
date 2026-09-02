"""Точка входа: поднимает цикл проверок и Telegram-бота в одном процессе."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from aiogram import Bot

from . import bot as bot_module
from .alerting import AlertPolicy
from .config import ConfigError, Settings, load
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

    bot = Bot(token=settings.telegram_token)
    monitor = Monitor(settings, store, probes, policy, bot, railway)

    try:
        if once:
            # Один прогон без бота: удобно для проверки конфигурации и для cron.
            for result in await monitor.tick():
                print(f"{result.health.value:8} {result.target}: {result.summary}")
            return 0

        dp = bot_module.build_dispatcher(settings, store, railway)
        await asyncio.gather(
            monitor.run_forever(),
            bot_module.start_polling(bot, dp),
        )
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
