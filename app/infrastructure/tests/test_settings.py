import pytest

from app.infrastructure.config.settings import Settings

SECRET = "s3cr3t-pa55word"
VALID = {"CRAFT_APP_DB_PASSWORD": SECRET, "CRAFT_DATABASE_PORT": "5432"}


@pytest.mark.parametrize(
    ("env", "field"),
    [
        ({**VALID, "CRAFT_DATABASE_PORT": "abc"}, "database_port"),
        ({"CRAFT_DATABASE_PORT": "5432"}, "app_db_password"),
        ({**VALID, "CRAFT_DATABSE_HOST": "x"}, "databse_host"),
        ({**VALID, "CRAFT_LOG_LEVEL": "LOUD"}, "log_level"),
    ],
)
def test_settings_fail_fast_names_field(env: dict[str, str], field: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        Settings.from_env(env)
    assert field in str(stopped.value)


def test_settings_never_print_secrets() -> None:
    settings = Settings.from_env(VALID)
    assert settings.database_port == 5432
    assert SECRET not in repr(settings) and SECRET not in str(settings)
    with pytest.raises(SystemExit) as stopped:
        Settings.from_env({**VALID, "CRAFT_DATABASE_PORT": "abc"})
    assert SECRET not in str(stopped.value) and "abc" not in str(stopped.value)


def test_admin_and_owner_passwords_are_not_app_settings() -> None:
    env = {**VALID, "CRAFT_DB_ADMIN_PASSWORD": "a", "CRAFT_OWNER_DB_PASSWORD": "o"}
    assert Settings.from_env(env).database_name == "craft_dev"


def test_invalid_enum_value_is_not_echoed() -> None:
    marker = "CRAFT_SECRET_INVALID_LEVEL"
    with pytest.raises(SystemExit) as raised:
        Settings.from_env({"CRAFT_APP_DB_PASSWORD": "synthetic", "CRAFT_LOG_LEVEL": marker})
    assert marker not in str(raised.value)


def test_invalid_enum_cannot_inject_a_diagnostic_path() -> None:
    marker = "$.private_payload_marker"
    with pytest.raises(SystemExit) as raised:
        Settings.from_env({"CRAFT_APP_DB_PASSWORD": "synthetic", "CRAFT_LOG_LEVEL": marker})
    diagnostic = str(raised.value)
    assert "private_payload_marker" not in diagnostic
    assert "log_level" in diagnostic


def test_malformed_allowed_origin_has_a_safe_diagnostic() -> None:
    marker = "private_origin_marker"
    with pytest.raises(SystemExit) as raised:
        Settings.from_env(
            {
                "CRAFT_APP_DB_PASSWORD": "synthetic",
                "CRAFT_ALLOWED_ORIGINS": "http://[" + marker + "]",
            }
        )
    diagnostic = str(raised.value)
    assert marker not in diagnostic and "allowed_origins" in diagnostic


@pytest.mark.parametrize(
    "value",
    [
        "http://remote.invalid/mcp",
        "http://synthetic:private@localhost/mcp",
        "http://localhost/mcp?private_marker",
        "http://[private_marker]/mcp",
    ],
    ids=["external", "credentials", "query", "invalid"],
)
def test_mcp_target_has_safe_loopback_validation(value):
    with pytest.raises(SystemExit) as stopped:
        Settings.from_env({**VALID, "CRAFT_MCP_URL": value})
    safe = value not in str(stopped.value) and "mcp_url" in str(stopped.value)
    assert safe


def test_compose_mcp_account_is_not_an_application_setting() -> None:
    settings = Settings.from_env({**VALID, "CRAFT_MCP_ACCOUNT": "synthetic-account"})
    assert settings.database_name == "craft_dev"
    assert not hasattr(settings, "mcp_account")


def test_cross_origin_browser_mode_is_opt_in() -> None:
    assert Settings.from_env(VALID).cross_origin_browser is False


def test_cross_origin_browser_mode_accepts_explicit_https_origins() -> None:
    configured = Settings.from_env(
        {
            **VALID,
            "CRAFT_CROSS_ORIGIN_BROWSER": "true",
            "CRAFT_ALLOWED_ORIGINS": "https://pages.craft.test,https://api.craft.test",
        }
    )
    assert configured.cross_origin_browser is True


@pytest.mark.parametrize(
    "origins",
    [
        "http://localhost:8000",
        "https://pages.craft.test,http://api.craft.test",
        "https://pages.craft.test/path",
        "https://invalid-origin-marker@192.0.2.1",
    ],
)
def test_cross_origin_browser_mode_rejects_non_https_or_malformed_origins(origins: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        Settings.from_env(
            {**VALID, "CRAFT_CROSS_ORIGIN_BROWSER": "true", "CRAFT_ALLOWED_ORIGINS": origins}
        )
    diagnostic = str(stopped.value)
    assert "allowed_origins" in diagnostic
    assert origins not in diagnostic and SECRET not in diagnostic


def test_disabled_cross_origin_browser_mode_retains_local_origin() -> None:
    configured = Settings.from_env({**VALID, "CRAFT_CROSS_ORIGIN_BROWSER": "false"})
    assert configured.cross_origin_browser is False
    assert configured.allowed_origins == "http://localhost:8000"


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:6379/0",
        "redis://localhost",
        "redis://user:secret@localhost:6379/0",
        "redis://localhost:70000/0",
        "redis://localhost:6379/not-a-db",
    ],
)
def test_queue_url_validation_has_safe_diagnostics(value: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        Settings.from_env({**VALID, "CRAFT_QUEUE_URL": value})
    diagnostic = str(stopped.value)
    assert "queue_url" in diagnostic
    assert value not in diagnostic and SECRET not in diagnostic
