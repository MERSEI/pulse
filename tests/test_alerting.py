from datetime import timedelta

import pytest

from pulse.alerting import AlertPolicy, mute
from pulse.models import Action, CheckResult, Health, TargetState, utcnow


def result(health, *, at=None, target="api"):
    return CheckResult(target=target, health=health, summary="", at=at or utcnow())


def fresh(target="api"):
    return TargetState(target=target)


class TestConfirmation:
    def test_single_failure_does_not_alert(self):
        policy = AlertPolicy(failure_threshold=2)
        decision = policy.evaluate(fresh(), result(Health.FAIL))
        assert decision.action is Action.NONE
        assert decision.state.consecutive_fails == 1

    def test_alerts_once_threshold_is_reached(self):
        policy = AlertPolicy(failure_threshold=2)
        state = fresh()
        assert policy.evaluate(state, result(Health.FAIL)).action is Action.NONE
        assert policy.evaluate(state, result(Health.FAIL)).action is Action.ALERT

    def test_flapping_never_alerts(self):
        # Отказ, успех, отказ, успех — счётчик каждый раз сбрасывается.
        policy = AlertPolicy(failure_threshold=2)
        state = fresh()
        for health in (Health.FAIL, Health.OK, Health.FAIL, Health.OK):
            assert policy.evaluate(state, result(health)).action is Action.NONE


class TestDeduplication:
    def test_open_incident_is_not_reannounced(self):
        policy = AlertPolicy(failure_threshold=1)
        state = fresh()
        assert policy.evaluate(state, result(Health.FAIL)).action is Action.ALERT
        for _ in range(5):
            assert policy.evaluate(state, result(Health.FAIL)).action is Action.NONE

    def test_reminder_fires_after_cooldown(self):
        policy = AlertPolicy(failure_threshold=1, remind_after=timedelta(hours=2))
        state = fresh()
        start = utcnow()
        assert (
            policy.evaluate(state, result(Health.FAIL, at=start)).action is Action.ALERT
        )

        soon = start + timedelta(minutes=30)
        assert policy.evaluate(state, result(Health.FAIL, at=soon)).action is Action.NONE

        later = start + timedelta(hours=2, minutes=1)
        assert (
            policy.evaluate(state, result(Health.FAIL, at=later)).action is Action.REMIND
        )

    def test_reminder_cooldown_restarts_after_each_reminder(self):
        policy = AlertPolicy(failure_threshold=1, remind_after=timedelta(hours=1))
        state = fresh()
        start = utcnow()
        policy.evaluate(state, result(Health.FAIL, at=start))
        policy.evaluate(state, result(Health.FAIL, at=start + timedelta(hours=1)))
        # Сразу после напоминания второго быть не должно.
        decision = policy.evaluate(
            state, result(Health.FAIL, at=start + timedelta(hours=1, minutes=5))
        )
        assert decision.action is Action.NONE


class TestRecovery:
    def test_recovery_is_announced_once(self):
        policy = AlertPolicy(failure_threshold=1)
        state = fresh()
        policy.evaluate(state, result(Health.FAIL))
        assert policy.evaluate(state, result(Health.OK)).action is Action.RECOVERED
        assert policy.evaluate(state, result(Health.OK)).action is Action.NONE

    def test_recovery_waits_for_its_own_threshold(self):
        policy = AlertPolicy(failure_threshold=1, recovery_threshold=3)
        state = fresh()
        policy.evaluate(state, result(Health.FAIL))
        assert policy.evaluate(state, result(Health.OK)).action is Action.NONE
        assert policy.evaluate(state, result(Health.OK)).action is Action.NONE
        assert policy.evaluate(state, result(Health.OK)).action is Action.RECOVERED

    def test_success_without_prior_incident_is_silent(self):
        policy = AlertPolicy()
        assert policy.evaluate(fresh(), result(Health.OK)).action is Action.NONE


class TestMute:
    def test_muted_target_does_not_alert(self):
        policy = AlertPolicy(failure_threshold=1)
        state = mute(fresh(), timedelta(hours=2))
        assert policy.evaluate(state, result(Health.FAIL)).action is Action.NONE

    def test_mute_expires(self):
        policy = AlertPolicy(failure_threshold=1)
        state = fresh()
        state.muted_until = utcnow() - timedelta(minutes=1)
        assert policy.evaluate(state, result(Health.FAIL)).action is Action.ALERT

    def test_mute_is_lifted_when_incident_closes(self):
        policy = AlertPolicy(failure_threshold=1)
        state = fresh()
        policy.evaluate(state, result(Health.FAIL))  # ALERT
        mute(state, timedelta(hours=4))
        policy.evaluate(state, result(Health.OK))  # RECOVERED
        assert state.muted_until is None


class TestUnknown:
    def test_unknown_does_not_count_as_failure(self):
        policy = AlertPolicy(failure_threshold=2)
        state = fresh()
        policy.evaluate(state, result(Health.FAIL))
        assert policy.evaluate(state, result(Health.UNKNOWN)).action is Action.NONE
        assert state.consecutive_fails == 1

    def test_unknown_does_not_close_an_open_incident(self):
        policy = AlertPolicy(failure_threshold=1)
        state = fresh()
        policy.evaluate(state, result(Health.FAIL))
        policy.evaluate(state, result(Health.UNKNOWN))
        assert state.alerted is True


def test_thresholds_must_be_positive():
    with pytest.raises(ValueError):
        AlertPolicy(failure_threshold=0)
