"""Карточки инцидентов в Telegram.

Формат сообщения — не украшение. Алерт читают с телефона и по нему решают,
вставать сейчас или до утра подождёт, поэтому в первой строке имя сервиса и
что с ним, а причина и кнопки — ниже.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .models import Action, CheckResult, Health

log = logging.getLogger(__name__)

ICON = {
    Action.ALERT: "🔴",
    Action.REMIND: "🟠",
    Action.RECOVERED: "🟢",
}

HEADLINE = {
    Action.ALERT: "упал",
    Action.REMIND: "всё ещё лежит",
    Action.RECOVERED: "поднялся",
}


def _escape(text: str) -> str:
    """Markdown в Telegram ломается на непарных * и _ из логов."""
    return text.replace("*", "").replace("_", "").replace("`", "'")


def render(action: Action, result: CheckResult, triage_text: str | None = None) -> str:
    lines = [
        f"{ICON.get(action, '')} *{_escape(result.target)}* — {HEADLINE.get(action, '')}",
        f"_{_escape(result.summary)}_",
    ]

    if triage_text:
        lines += ["", triage_text]
    elif result.detail and action is not Action.RECOVERED:
        lines += ["", f"```\n{_escape(result.detail[:400])}\n```"]

    return "\n".join(lines)


def keyboard(result: CheckResult, *, can_restart: bool) -> InlineKeyboardMarkup | None:
    """Кнопки под алертом.

    Рестарт уводит на отдельное подтверждение: это действие на проде, и
    случайное касание в кармане не должно его запускать.
    """
    row = [
        InlineKeyboardButton(
            text="Заглушить 2ч", callback_data=f"mute:{result.target}:120"
        )
    ]
    if result.ref:
        row.insert(
            0, InlineKeyboardButton(text="Логи", callback_data=f"logs:{result.ref}")
        )
    rows = [row]

    if can_restart and result.ref:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Перезапустить…", callback_data=f"restart?:{result.ref}"
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def status_report(states, incidents) -> str:
    """Ответ на /status: что сейчас с каждым таргетом."""
    if not states:
        return "Пока ни одной проверки не прошло."

    icon = {Health.OK: "🟢", Health.FAIL: "🔴", Health.UNKNOWN: "⚪"}
    lines = ["*Состояние сервисов*", ""]

    for state in states:
        suffix = ""
        if state.is_muted():
            suffix = " (заглушён)"
        elif state.alerted:
            suffix = " (инцидент открыт)"
        lines.append(f"{icon.get(state.health, '⚪')} {_escape(state.target)}{suffix}")

    if incidents:
        lines += ["", "*Последние инциденты*", ""]
        for row in incidents[:5]:
            opened = row["opened_at"][:16].replace("T", " ")
            mark = "закрыт" if row["closed_at"] else "открыт"
            lines.append(f"`{opened}` {_escape(row['target'])} — {mark}")

    return "\n".join(lines)


async def send(
    bot: Bot,
    chat_id: str,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await bot.send_message(
            chat_id,
            text,
            parse_mode="Markdown",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # доставка не должна ронять цикл проверок
        log.error("Не отправилось в Telegram: %s", exc)
