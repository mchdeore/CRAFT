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
    seed = commands.add_parser("seed")
    seed.add_argument("set", choices=["base"])
    index = commands.add_parser("index")
    index.add_argument("--rebuild", action="store_true")
    call = commands.add_parser("call")
    call.add_argument("operation")
    call.add_argument("--as", dest="username", required=True)
    call.add_argument("arguments", nargs="?", default="{}")
    provider = commands.add_parser("provider")
    provider.add_argument("action", choices=["scan", "put", "rm", "log"])
    provider.add_argument("path", nargs="?")
    provider.add_argument("file", nargs="?")
    users = commands.add_parser("users")
    users.add_argument("action", choices=["grant", "revoke"])
    users.add_argument("username")
    users.add_argument("mission")
    users.add_argument("--silo")
    users.add_argument("--as", dest="actor", default="admin")
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
    elif args.command in {"seed", "index", "provider", "call", "users"}:
        import json
        import sys
        from pathlib import Path

        import msgspec

        from app.infrastructure.config.settings import Settings
        from app.infrastructure.logging import configure_logging

        settings = Settings.from_env()
        configure_logging(settings.log_level, sys.stderr)
        if args.command == "seed":
            from app.seed.base import seed

            print(json.dumps(seed(settings)))
        elif args.command == "index":
            from app.library.index import build

            print(json.dumps(build(settings, args.rebuild)))
        elif args.command == "provider":
            from app.infrastructure.provider.simulated import SimulatedProvider

            provider = SimulatedProvider(settings.provider_root)
            if args.action == "scan":
                print(json.dumps({"changes": len(provider.scan())}))
            elif args.action == "put":
                if not args.path or not args.file:
                    parser.error("put requires a path and file")
                provider.write_content(args.path, Path(args.file).read_bytes())
            elif args.action == "rm":
                item = next((i for i in provider.items().values() if i["path"] == args.path), None)
                if item is None:
                    parser.error("unknown provider item")
                provider.delete_item(item["item"])
            else:
                changes, cursor = provider.list_changes(0)
                print(json.dumps({"changes": changes, "cursor": cursor}))
        else:
            from dataclasses import replace

            from app.infrastructure.mcp.stdio import local_caller

            caller = replace(
                local_caller(args.actor if args.command == "users" else args.username, settings),
                origin="python",
            )
            if args.command == "users":
                from app.accounts.handlers import GrantMission, grant, revoke

                (revoke if args.action == "revoke" else grant)(
                    caller,
                    GrantMission(username=args.username, mission=args.mission, silo=args.silo),
                )
            else:
                from app.infrastructure.operations.registry import registry

                operation = registry.get(args.operation)
                if operation is None:
                    parser.error("unknown operation")
                result = operation.function(caller, msgspec.json.decode(args.arguments))
                print(msgspec.json.encode(result).decode())
    else:
        parser.print_help()
