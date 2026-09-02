from datetime import timedelta

from pulse.alerting import mute
from pulse.models import Health, TargetState, utcnow
from pulse.storage import Store


def test_unknown_target_loads_clean(tmp_path):
    store = Store(tmp_path / "pulse.db")
    state = store.load("railway/app/api")
    assert state.target == "railway/app/api"
    assert state.health is Health.UNKNOWN
    assert state.alerted is False


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "pulse.db"
    state = TargetState(
        target="api", health=Health.FAIL, consecutive_fails=3, alerted=True
    )
    state.last_alert_at = utcnow()
    Store(path).save(state)

    # Новый объект Store = новый процесс после рестарта контейнера.
    restored = Store(path).load("api")
    assert restored.health is Health.FAIL
    assert restored.consecutive_fails == 3
    assert restored.alerted is True
    assert restored.last_alert_at is not None


def test_save_is_idempotent_upsert(tmp_path):
    store = Store(tmp_path / "pulse.db")
    store.save(TargetState(target="api", consecutive_fails=1))
    store.save(TargetState(target="api", consecutive_fails=7))
    assert store.load("api").consecutive_fails == 7
    assert len(store.all_states()) == 1


def test_mute_deadline_round_trips(tmp_path):
    store = Store(tmp_path / "pulse.db")
    store.save(mute(TargetState(target="api"), timedelta(hours=2)))
    assert store.load("api").is_muted() is True


def test_incident_lifecycle(tmp_path):
    store = Store(tmp_path / "pulse.db")
    opened = utcnow()
    store.open_incident("api", opened, "HTTP 502", triage="OOM")

    incidents = store.recent_incidents()
    assert len(incidents) == 1
    assert incidents[0]["closed_at"] is None
    assert incidents[0]["triage"] == "OOM"

    store.close_incident("api", utcnow())
    assert store.recent_incidents()[0]["closed_at"] is not None


def test_close_touches_only_the_latest_open_incident(tmp_path):
    store = Store(tmp_path / "pulse.db")
    first = utcnow() - timedelta(days=1)
    store.open_incident("api", first, "старое падение", None)
    store.close_incident("api", utcnow() - timedelta(hours=20))

    store.open_incident("api", utcnow(), "новое падение", None)
    store.close_incident("api", utcnow())

    assert all(row["closed_at"] for row in store.recent_incidents())


def test_creates_parent_directory(tmp_path):
    store = Store(tmp_path / "nested" / "dir" / "pulse.db")
    store.save(TargetState(target="api"))
    assert (tmp_path / "nested" / "dir" / "pulse.db").exists()
