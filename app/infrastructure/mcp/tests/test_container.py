"""A disposable MCP stack returns a cited answer without altering shared test state."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.infrastructure.config.env import read_env
from app.infrastructure.paths import ROOT


@pytest.fixture
def container_provider():
    root = ROOT / "var"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="mcp-test-provider-", dir=root) as directory:
        provider = Path(directory)
        provider.chmod(0o755)
        yield provider


@pytest.mark.live
def test_mcp_container_lists_and_answers(container_provider):
    if shutil.which("docker") is None:
        pytest.skip("Docker is missing")
    configured = read_env()
    if not configured.get("CRAFT_MODEL_KEY") or not configured.get("CRAFT_MODEL_ENDPOINT"):
        pytest.skip("The model endpoint and key are not configured")
    provider = container_provider
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        database_port = port.getsockname()[1]
    environment = os.environ | {
        "CRAFT_DATABASE_NAME": "craft_test",
        "CRAFT_DATABASE_HOST": "localhost",
        "CRAFT_DATABASE_PORT": str(database_port),
        "CRAFT_MCP_URL": "http://127.0.0.1:8000/mcp",
        "CRAFT_PROVIDER_ROOT": str(provider),
    }
    with TemporaryDirectory(prefix="craft-B22-", dir="/tmp") as temporary:
        override = os.path.join(temporary, "compose.yaml")
        with open(override, "w") as file:
            file.write("services:\n  app:\n    ports: !reset []\n")
        compose = [
            "docker",
            "compose",
            "-p",
            "craft-b22-" + uuid4().hex[:12],
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            override,
        ]
        try:
            for command in [
                compose + ["up", "-d", "--build", "--wait"],
                compose + ["--profile", "mcp", "build", "mcp"],
                ["uv", "run", "alembic", "upgrade", "head"],
                ["uv", "run", "craft", "seed", "demo"],
            ]:
                prepared = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                assert prepared.returncode == 0, "Container preparation failed"
            inspected = subprocess.run(
                compose
                + [
                    "exec",
                    "-T",
                    "app",
                    "python",
                    "-c",
                    "from pathlib import Path\n"
                    "import json\n"
                    "p = Path('/provider/.provider/items.jsonl')\n"
                    "count = len(p.read_text().splitlines()) if p.exists() else 0\n"
                    "print(json.dumps({'items': count}))\n",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert inspected.returncode == 0, "Container provider inspection failed"
            assert json.loads(inspected.stdout)["items"] > 0, "Generated provider was not mounted"
            parameters = StdioServerParameters(
                command="docker",
                args=compose[1:] + ["--profile", "mcp", "run", "--rm", "-T", "mcp"],
                env=environment,
                cwd=str(ROOT),
            )

            async def execute():
                async with (
                    stdio_client(parameters) as (incoming, outgoing),
                    ClientSession(incoming, outgoing) as session,
                ):
                    await session.initialize()
                    listed = await session.list_tools()
                    assert {"ask_agent", "write_note", "search"}.issubset(
                        {tool.name for tool in listed.tools}
                    )
                    evidence = await session.call_tool("search", {"query": "Peak power"})
                    assert not evidence.isError, "Container search failed"
                    assert evidence.structuredContent and evidence.structuredContent.get("hits"), (
                        "Container search returned no readable evidence"
                    )
                    result = await session.call_tool("ask_agent", {"question": "Peak power"})
                    content = result.structuredContent or {}
                    assert not result.isError and content.get("answer") and content.get("citations")
                    checked = await session.call_tool(
                        "check_citation", {"handle": content["citations"][0]["handle"]}
                    )
                    assert not checked.isError and checked.structuredContent is not None
                    assert checked.structuredContent.get("status") == "current"

            asyncio.run(asyncio.wait_for(execute(), timeout=120))
        finally:
            subprocess.run(
                compose + ["--profile", "mcp", "down", "--volumes", "--rmi", "local"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                timeout=60,
                check=True,
            )
