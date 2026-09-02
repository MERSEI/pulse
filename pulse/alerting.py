"""Решение о том, будить ли человека.

Это ядро Pulse. Мониторинг, который шлёт сообщение на каждый неудачный
запрос, читать перестают через день, и тогда он не мониторинг, а шум.
Здесь три механизма против шума:

* подтверждение — алерт только после N подряд неудачных проверок,
  одиночный таймаут сети не будит никого;
* дедуп — пока инцидент открыт, повтор не шлётся, вместо него редкое
  напоминание по cooldown;
* мьют — таргет можно заглушить до времени, не выключая проверку.

Отдельный случай — Health.UNKNOWN: проба не смогла узнать, жив ли сервис
(протух токен, лимит API, нет сети). Это не падение, и оно не двигает
счётчик отказов ни в одну сторону.
"""

from __future__ import annotations

from datetime import timedelta

from .models import Action, CheckResult, Decision, Health, TargetState, utcnow


class AlertPolicy:
    """Правила перехода состояния таргета.

    :param failure_threshold: сколько подряд отказов подтверждают инцидент
    :param recovery_threshold: сколько подряд успехов закрывают его
    :param remind_after: пауза перед напоминанием об открытом инциденте
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        recovery_threshold: int = 1,
        remind_after: timedelta = timedelta(hours=2),
    ):
        if failure_threshold < 1 or recovery_threshold < 1:
            raise ValueError("Пороги должны быть не меньше 1")
        self.failure_threshold = failure_threshold
        self.recovery_threshold = recovery_threshold
        self.remind_after = remind_after

    def evaluate(self, state: TargetState, result: CheckResult) -> Decision:
        """Обновить состояние по результату проверки и решить, что делать."""
        now = result.at

        if result.health is Health.UNKNOWN:
            # Ничего не узнали — счётчики не трогаем, чтобы сломанный токен
            # не выглядел как падение сервиса и не закрывал открытый инцидент.
            return Decision(Action.NONE, state, "проба не дала ответа")

        if result.health is Health.FAIL:
            state.consecutive_fails += 1
            state.consecutive_oks = 0
            state.health = Health.FAIL
            return self._on_failure(state, now)

        state.consecutive_oks += 1
        state.consecutive_fails = 0
        state.health = Health.OK
        return self._on_success(state)

    def _on_failure(self, state: TargetState, now) -> Decision:
        if state.consecutive_fails < self.failure_threshold:
            return Decision(
                Action.NONE,
                state,
                f"отказ {state.consecutive_fails}/{self.failure_threshold}, ждём подтверждения",
            )

        if state.is_muted(now):
            return Decision(Action.NONE, state, "таргет заглушён")

        if not state.alerted:
            state.alerted = True
            state.last_alert_at = now
            return Decision(Action.ALERT, state, "инцидент подтверждён")

        if (
            state.last_alert_at is not None
            and now - state.last_alert_at >= self.remind_after
        ):
            state.last_alert_at = now
            return Decision(Action.REMIND, state, "инцидент всё ещё открыт")

        return Decision(Action.NONE, state, "об инциденте уже сообщено")

    def _on_success(self, state: TargetState) -> Decision:
        if not state.alerted:
            return Decision(Action.NONE, state, "штатно")

        if state.consecutive_oks < self.recovery_threshold:
            return Decision(
                Action.NONE,
                state,
                f"восстановление {state.consecutive_oks}/{self.recovery_threshold}",
            )

        state.alerted = False
        state.last_alert_at = None
        # Мьют снимается вместе с инцидентом: он глушил конкретное падение,
        # а не таргет вообще.
        state.muted_until = None
        return Decision(Action.RECOVERED, state, "сервис восстановился")


def mute(state: TargetState, duration: timedelta) -> TargetState:
    """Заглушить таргет на срок. Проверки продолжают идти, сообщения — нет."""
    state.muted_until = utcnow() + duration
    return state
