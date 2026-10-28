"""Validate the shipped config using Jupyter's real trait types, without starting a server."""

import io
import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stderr
from importlib import import_module, util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def generated_config(origin):
    config = SimpleNamespace(
        ServerApp=SimpleNamespace(),
        IdentityProvider=SimpleNamespace(),
        PasswordIdentityProvider=SimpleNamespace(),
        FileContentsManager=SimpleNamespace(),
        Application=SimpleNamespace(),
    )
    with tempfile.TemporaryDirectory() as directory:
        with patch.dict(
            os.environ,
            {
                "USER_ROOT": str(Path(directory).resolve()),
                "JUPYTER_TOKEN": "generated-private-token",
                "CRAFT_UI_ORIGIN": origin,
            },
            clear=True,
        ):
            runpy.run_path(
                str(Path(__file__).parents[1] / "jupyter_server_config.py"),
                init_globals={"get_config": lambda: config},
            )
    return config


class JupyterConfigTest(unittest.TestCase):
    def test_error_defaults_keep_the_exact_allowed_ui_origin(self):
        for origin in ("http://127.0.0.1:5173/", "https://localhost:5174"):
            with self.subTest(origin=origin):
                config = generated_config(origin)
                allowed = origin.rstrip("/")
                self.assertEqual(config.ServerApp.allow_origin, allowed)
                self.assertTrue(config.ServerApp.allow_credentials)
                self.assertEqual(
                    config.ServerApp.tornado_settings["headers"],
                    {
                        "Content-Security-Policy": f"frame-ancestors 'self' {allowed}",
                        "Access-Control-Allow-Origin": allowed,
                        "Access-Control-Allow-Credentials": "true",
                    },
                )
                self.assertFalse(config.ServerApp.disable_check_xsrf)
                self.assertFalse(config.FileContentsManager.allow_hidden)

    def test_serverapp_accepts_config_and_suppresses_info_logging(self):
        if any(util.find_spec(name) is None for name in ("jupyter_server", "traitlets")):
            self.skipTest("Optional JupyterLab dependencies are absent; run in the lab environment")
        ServerApp = import_module("jupyter_server.serverapp").ServerApp
        Config = import_module("traitlets.config").Config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.dict(
                os.environ,
                {
                    "USER_ROOT": str(root),
                    "JUPYTER_TOKEN": "generated-private-token",
                    "JUPYTER_PASSWORD_HASH": "",
                    "CRAFT_UI_ORIGIN": "http://127.0.0.1:5173",
                },
            ):
                config = Config()
                runpy.run_path(
                    str(Path(__file__).parents[1] / "jupyter_server_config.py"),
                    init_globals={"get_config": lambda: config},
                )
                output = io.StringIO()
                with redirect_stderr(output):
                    application = ServerApp(config=config)
                    application.log.info("generated-info")
                    application.log.warning("generated-warning")
                self.assertEqual(application.root_dir, str(root))
                self.assertNotIn("generated-info", output.getvalue())
                self.assertIn("generated-warning", output.getvalue())

    def test_configured_password_authenticates_without_removing_token(self):
        if any(util.find_spec(name) is None for name in ("jupyter_server", "traitlets")):
            self.skipTest("Optional JupyterLab dependencies are absent; run in the lab environment")
        ServerApp = import_module("jupyter_server.serverapp").ServerApp
        Config = import_module("traitlets.config").Config
        passwd = import_module("jupyter_server.auth.security").passwd

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.dict(
                os.environ,
                {
                    "USER_ROOT": str(root),
                    "JUPYTER_TOKEN": "generated-private-token",
                    "JUPYTER_PASSWORD_HASH": passwd("generated-correct-password"),
                    "CRAFT_UI_ORIGIN": "http://127.0.0.1:5173",
                },
            ):
                config = Config()
                runpy.run_path(
                    str(Path(__file__).parents[1] / "jupyter_server_config.py"),
                    init_globals={"get_config": lambda: config},
                )
                application = ServerApp(config=config)
                provider = application.identity_provider_class(config=config, parent=application)
                self.assertTrue(provider.passwd_check("generated-correct-password"))
                self.assertFalse(provider.passwd_check("generated-wrong-password"))
                self.assertEqual(provider.token, "generated-private-token")


if __name__ == "__main__":
    unittest.main()


class HubConfigTest(unittest.TestCase):
    def test_under_the_hub_no_token_is_needed_and_the_control_server_gets_its_own(self):
        settings = SimpleNamespace(
            ServerApp=SimpleNamespace(),
            IdentityProvider=SimpleNamespace(),
            PasswordIdentityProvider=SimpleNamespace(),
            FileContentsManager=SimpleNamespace(),
            Application=SimpleNamespace(),
        )
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as control:
            with patch.dict(
                os.environ,
                {
                    "USER_ROOT": str(Path(root).resolve()),
                    "JUPYTERHUB_API_TOKEN": "generated-server-token",
                    "JUPYTERHUB_SERVICE_PREFIX": "/user/generated/",
                    "CRAFT_CONTROL_DIR": control,
                },
                clear=True,
            ):
                runpy.run_path(
                    str(Path(__file__).parents[1] / "jupyter_server_config.py"),
                    init_globals={"get_config": lambda: settings},
                )
            token = Path(control, "token")
            self.assertEqual(token.read_text(), "generated-server-token")
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                Path(control, "url").read_text(), "http://127.0.0.1:8888/user/generated/"
            )
        self.assertFalse(hasattr(settings.IdentityProvider, "token"))
