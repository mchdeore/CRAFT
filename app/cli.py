"""The craft command. S0: --version. Later: seed, users, operations, call."""

import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="craft", description="CRAFT backend tools")
    parser.add_argument("--version", action="version", version=f"craft {version('craft')}")
    commands = parser.add_subparsers(dest="command")
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--as", dest="username", required=True)
    commands.add_parser("operations")
    args = parser.parse_args(argv)
    if args.command == "mcp":
        from app.infrastructure.mcp.stdio import run_stdio

        run_stdio(args.username)
    elif args.command == "operations":
        from app.infrastructure.operations.registry import registry

        for operation in registry.values():
            print(
                operation.name,
                operation.method or "-",
                operation.path or "-",
                operation.tool_name if operation.tool else "-",
                operation.access,
                operation.effects,
            )
    else:
        parser.print_help()
