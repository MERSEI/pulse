"""Telegram-бот: ответы на команды и кнопки под алертами.

Бот отвечает только владельцу — chat_id из конфигурации. Иначе любой, кто
найдёт бота, сможет перезапускать чужие сервисы.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import notify
from .alerting import mute
from .config import Settings
from .probes.railway import RailwayClient, RailwayError
from .storage import Store

log = logging.getLogger(__name__)

RESTART_MUTATION = """
mutation deploymentRestart($id: String!) {
  deploymentRestart(id: $id)
}
"""


def build_dispatcher(
    settings: Settings, store: Store, railway: RailwayClient | None
) -> Dispatcher:
    dp = Dispatcher()
    owner = str(settings.telegram_chat_id)

    def is_owner(event: Message | CallbackQuery) -> bool:
        chat_id = (
            event.chat.id if isinstance(event, Message) else event.message.chat.id
        )
        return str(chat_id) == owner

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if not is_owner(message):
            return
        await message.answer(
            notify.status_report(store.all_states(), store.recent_incidents()),
            parse_mode="Markdown",
        )

    @dp.message(Command("mute"))
    async def cmd_mute(message: Message) -> None:
        """/mute <таргет> <минуты> — заглушить конкретный сервис."""
        if not is_owner(message):
            return

        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer("Формат: `/mute railway/proj/api 120`", parse_mode="Markdown")
            return

        target, raw_minutes = parts[1], parts[2]
        try:
            minutes = int(raw_minutes)
        except ValueError:
            await message.answer(f"«{raw_minutes}» — это не число минут.")
            return

        state = store.load(target)
        store.save(mute(state, timedelta(minutes=minutes)))
        await message.answer(f"{target} заглушён на {minutes} мин.")

    @dp.callback_query(F.data.startswith("mute:"))
    async def on_mute(callback: CallbackQuery) -> None:
        if not is_owner(callback):
            return
        _, target, minutes = callback.data.split(":", 2)
        state = store.load(target)
        store.save(mute(state, timedelta(minutes=int(minutes))))
        await callback.answer(f"Заглушено на {minutes} мин")

    @dp.callback_query(F.data.startswith("logs:"))
    async def on_logs(callback: CallbackQuery) -> None:
        if not is_owner(callback):
            return
        if railway is None:
            await callback.answer("Railway не подключён", show_alert=True)
            return

        deployment_id = callback.data.split(":", 1)[1]
        await callback.answer("Тяну логи…")
        try:
            lines = await railway.logs(deployment_id, limit=50)
        except RailwayError as exc:
            await callback.message.answer(f"Логи не достались: {exc}")
            return

        tail = "\n".join(lines[-30:]) or "Лог пуст."
        await callback.message.answer(f"```\n{tail[:3500]}\n```", parse_mode="Markdown")

    @dp.callback_query(F.data.startswith("restart?:"))
    async def on_restart_ask(callback: CallbackQuery) -> None:
        """Первое касание только спрашивает — рестарт идёт на прод."""
        if not is_owner(callback):
            return
        deployment_id = callback.data.split(":", 1)[1]
        await callback.message.answer(
            "Перезапустить деплой? Это действие на проде.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Да, перезапустить",
                            callback_data=f"restart!:{deployment_id}",
                        ),
                        InlineKeyboardButton(text="Отмена", callback_data="cancel"),
                    ]
                ]
            ),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("restart!:"))
    async def on_restart_confirm(callback: CallbackQuery) -> None:
        if not is_owner(callback):
            return
        if railway is None:
            await callback.answer("Railway не подключён", show_alert=True)
            return

        deployment_id = callback.data.split(":", 1)[1]
        await callback.answer("Перезапускаю…")
        try:
            await railway.query(RESTART_MUTATION, {"id": deployment_id})
        except RailwayError as exc:
            await callback.message.answer(f"Не вышло: {exc}")
            return
        await callback.message.answer("Перезапуск отправлен.")

    @dp.callback_query(F.data == "cancel")
    async def on_cancel(callback: CallbackQuery) -> None:
        await callback.answer("Отменено")
        await callback.message.edit_text("Отменено.")

    return dp


async def start_polling(bot: Bot, dp: Dispatcher) -> None:
    await dp.start_polling(bot, handle_signals=False)
