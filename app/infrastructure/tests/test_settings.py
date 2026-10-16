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
