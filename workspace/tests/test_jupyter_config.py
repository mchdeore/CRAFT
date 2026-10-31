"""Validate the shipped config using Jupyter's real trait types, without starting a server."""

import io
import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stderr
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def generated_config(origin):
    config = SimpleNamespace(
        ServerApp=SimpleNamespace(),
        FileContentsManager=SimpleNamespace(),
        Application=SimpleNamespace(),
    )
    with tempfile.TemporaryDirectory() as directory:
        with patch.dict(
            os.environ,
            {
                "USER_ROOT": str(Path(directory).resolve()),
                "JUPYTERHUB_API_TOKEN": "generated-server-token",
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
        ServerApp = import_module("jupyter_server.serverapp").ServerApp
        Config = import_module("traitlets.config").Config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.dict(
                os.environ,
                {
                    "USER_ROOT": str(root),
                    "JUPYTERHUB_API_TOKEN": "generated-server-token",
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

    def test_without_the_hubs_token_the_config_refuses_even_a_jupyter_token(self):
        with tempfile.TemporaryDirectory() as root:
            environment = {
                "USER_ROOT": str(Path(root).resolve()),
                "JUPYTER_TOKEN": "generated-private-token",  # the token mode before the Hub
            }
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(ValueError, "JUPYTERHUB_API_TOKEN"):
                    runpy.run_path(
                        str(Path(__file__).parents[1] / "jupyter_server_config.py"),
                        init_globals={"get_config": SimpleNamespace},
                    )

    def test_no_token_is_set_and_the_control_server_gets_the_hub_token(self):
        settings = SimpleNamespace(
            ServerApp=SimpleNamespace(),
            IdentityProvider=SimpleNamespace(),
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
