import pytest

from pulse.config import ConfigError, load, load_dotenv, load_targets


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "PULSE_TELEGRAM_TOKEN",
        "PULSE_TELEGRAM_CHAT_ID",
        "RAILWAY_TOKEN",
        "VERCEL_TOKEN",
        "VERCEL_TEAM_ID",
        "PULSE_INTERVAL_SECONDS",
        "PULSE_FAILURE_THRESHOLD",
        "PULSE_DB_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


class TestDotenv:
    def test_reads_key_values(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text('PULSE_TELEGRAM_TOKEN="abc"\nVERCEL_TOKEN=xyz\n', encoding="utf-8")
        load_dotenv(env)
        import os

        assert os.environ["PULSE_TELEGRAM_TOKEN"] == "abc"
        assert os.environ["VERCEL_TOKEN"] == "xyz"

    def test_does_not_override_real_environment(self, tmp_path, monkeypatch):
        # Переданное при запуске всегда сильнее файла.
        monkeypatch.setenv("RAILWAY_TOKEN", "from-shell")
        env = tmp_path / ".env"
        env.write_text("RAILWAY_TOKEN=from-file\n", encoding="utf-8")
        load_dotenv(env)
        import os

        assert os.environ["RAILWAY_TOKEN"] == "from-shell"

    def test_skips_comments_and_blanks(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# коммент\n\nVERCEL_TEAM_ID=team_1\nбез-знака-равно\n", encoding="utf-8")
        load_dotenv(env)
        import os

        assert os.environ["VERCEL_TEAM_ID"] == "team_1"

    def test_missing_file_is_not_an_error(self, tmp_path):
        load_dotenv(tmp_path / "nope.env")


class TestTelegramOptional:
    def test_config_loads_without_telegram(self, tmp_path):
        # Разовый прогон должен быть доступен до того, как заведён бот.
        settings = load(tmp_path / "targets.yml")
        assert settings.telegram_enabled is False

    def test_both_halves_required(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PULSE_TELEGRAM_TOKEN", "t")
        assert load(tmp_path / "targets.yml").telegram_enabled is False
        monkeypatch.setenv("PULSE_TELEGRAM_CHAT_ID", "1")
        assert load(tmp_path / "targets.yml").telegram_enabled is True


class TestTargets:
    def test_missing_file_yields_nothing(self, tmp_path):
        assert load_targets(tmp_path / "nope.yml") == ([], [], [])

    def test_parses_all_three_sections(self, tmp_path):
        path = tmp_path / "targets.yml"
        path.write_text(
            "http:\n"
            "  - name: api\n"
            "    url: https://x.test\n"
            "    expect: [200, 401]\n"
            "    expect_body: hello\n"
            "railway:\n  projects: [proj-1]\n"
            "vercel:\n  projects: [prj_1, prj_2]\n",
            encoding="utf-8",
        )
        endpoints, railway, vercel = load_targets(path)
        assert endpoints[0].expect == (200, 401)
        assert endpoints[0].expect_body == "hello"
        assert railway == ["proj-1"]
        assert vercel == ["prj_1", "prj_2"]

    def test_endpoint_without_url_is_rejected_loudly(self, tmp_path):
        path = tmp_path / "targets.yml"
        path.write_text("http:\n  - name: api\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_targets(path)

    def test_provider_enabled_needs_both_token_and_projects(self, tmp_path, monkeypatch):
        path = tmp_path / "targets.yml"
        path.write_text("vercel:\n  projects: [prj_1]\n", encoding="utf-8")

        assert load(path).vercel_enabled is False  # проекты есть, токена нет
        monkeypatch.setenv("VERCEL_TOKEN", "tok")
        assert load(path).vercel_enabled is True


def test_bad_integer_is_reported_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSE_FAILURE_THRESHOLD", "два")
    with pytest.raises(ConfigError, match="целым числом"):
        load(tmp_path / "targets.yml")
