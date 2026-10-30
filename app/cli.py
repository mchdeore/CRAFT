"""The craft command: mcp, worker, queues, usage, operations, call, provider, users, missions
and id."""

import argparse
import re
from importlib.metadata import version


def parse_since(value: str) -> float:
    """Turn 24h / 1h / 7d into seconds for craft queues report."""
    match = re.fullmatch(r"(\d+)([smhd])", value.strip().lower())
    if match is None:
        raise argparse.ArgumentTypeError("use a duration like 24h, 1h, 30m or 7d")
    amount, unit = int(match[1]), match[2]
    return float(amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="craft", description="CRAFT backend tools")
    parser.add_argument("--version", action="version", version=f"craft {version('craft')}")
    commands = parser.add_subparsers(dest="command")
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--as", dest="username", required=True)
    commands.add_parser("operations")
    worker = commands.add_parser("worker")
    worker.add_argument("--check", action="store_true", help="exit 1 without a recent heartbeat")
    queues = commands.add_parser("queues", help="job queue measurements")
    queues_commands = queues.add_subparsers(dest="queues_action")
    report = queues_commands.add_parser("report", help="wait/run percentiles and saturation")
    report.add_argument(
        "--since",
        type=parse_since,
        default=parse_since("24h"),
        help="lookback window (default 24h)",
    )
    commands.add_parser("usage", help="model tokens and spend in the last 24 hours and in all")
    call = commands.add_parser("call")
    call.add_argument("operation")
    call.add_argument("--as", dest="username", required=True)
    call.add_argument("arguments", nargs="?", default="{}")
    provider = commands.add_parser("provider")
    provider.add_argument("action", choices=["scan", "put", "rm", "log", "serve"])
    provider.add_argument("--port", type=int, default=8200, help="serve: the LiveLink API port")
    provider.add_argument("path", nargs="?")
    provider.add_argument("file", nargs="?")
    users = commands.add_parser("users")
    users.add_argument(
        "action",
        choices=["grant", "revoke", "add", "list", "promote", "reset", "disable", "approve"],
    )
    users.add_argument("username", nargs="?", help="for approve: the email")
    users.add_argument("mission", nargs="?")
    users.add_argument("--silo")
    users.add_argument("--email", help="add: sign in at the identity provider with this email")
    users.add_argument("--as", dest="actor", default="admin")
    missions = commands.add_parser("missions")
    missions.add_argument("action", choices=["stage"])
    missions.add_argument("mission")
    missions.add_argument("stage")
    identity = commands.add_parser("id", help="craft-id, the stand-in for Entra")
    identity.add_argument(
        "action", choices=["serve", "users", "clients"], help="users and clients take a verb"
    )
    identity.add_argument("verb", nargs="?", choices=["add", "disable", "reset-password"])
    identity.add_argument("name", nargs="?", help="username, or the client's name")
    identity.add_argument("--email")
    identity.add_argument("--display-name")
    identity.add_argument("--redirect-uri", action="append", default=[])
    identity.add_argument("--port", type=int, default=8100)
    args = parser.parse_args(argv)
    if args.command == "id":
        from app.identity.cli import run

        run(parser, args)
    elif args.command == "mcp":
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
    elif args.command == "worker" and args.check:
        from app.infrastructure.queue import check_worker

        raise SystemExit(0 if check_worker() else 1)
    elif args.command == "queues":
        import json
        import sys

        from app.infrastructure.config.settings import Settings
        from app.infrastructure.database.engine import make_engine, make_sessions
        from app.infrastructure.logging import configure_logging
        from app.jobs.handlers import queue_report

        if args.queues_action != "report":
            parser.parse_args(["queues", "--help"])
            raise SystemExit(2)
        settings = Settings.from_env()
        configure_logging(settings.log_level, sys.stderr)
        engine = make_engine(settings)
        try:
            print(json.dumps(queue_report(make_sessions(engine), args.since), sort_keys=True))
        finally:
            engine.dispose()
    elif args.command == "usage":
        import json
        import sys

        from app.agent.usage import report
        from app.infrastructure.config.settings import Settings
        from app.infrastructure.database.engine import make_engine, make_sessions
        from app.infrastructure.logging import configure_logging

        settings = Settings.from_env()
        configure_logging(settings.log_level, sys.stderr)
        engine = make_engine(settings)
        try:
            print(json.dumps(report(settings, make_sessions(engine)), sort_keys=True))
        finally:
            engine.dispose()
    elif args.command in {"worker", "provider", "call", "users", "missions"}:
        import json
        import sys
        from pathlib import Path

        import msgspec

        from app.infrastructure.config.settings import Settings
        from app.infrastructure.logging import configure_logging

        settings = Settings.from_env()
        configure_logging(settings.log_level, sys.stderr)
        if args.command == "missions":
            from app.cube.handlers import change_stage

            change_stage(settings, args.mission, args.stage)
        elif args.command == "worker":
            from app.infrastructure.queue import run_worker

            run_worker(settings)
        elif args.command == "provider":
            from app.infrastructure.provider import get_provider, source_provider

            if args.action == "serve":
                import uvicorn

                from app.infrastructure.provider.livelink_api import livelink_api

                if not settings.provider_token:
                    parser.error("set CRAFT_PROVIDER_TOKEN: the API needs a ticket")
                # The LiveLink-shaped API over the provider folder's originals.
                uvicorn.run(
                    livelink_api(settings.provider_root, settings.provider_token),
                    host="127.0.0.1",
                    port=args.port,
                    log_level="warning",
                )
                return
            provider = source_provider(settings)
            # Writes always go to the simulated folder; the LiveLink adapter only reads.
            writer = get_provider(settings.provider_root)
            if args.action == "scan":
                print(json.dumps({"changes": len(provider.scan())}))
            elif args.action == "put":
                if not args.path or not args.file:
                    parser.error("put requires a path and file")
                writer.write_content(args.path, Path(args.file).read_bytes())
            elif args.action == "rm":
                item = next((i for i in writer.items().values() if i["path"] == args.path), None)
                if item is None:
                    parser.error("unknown provider item")
                writer.delete_item(item["item"])
            else:
                changes, cursor = provider.list_changes(0)
                print(json.dumps({"changes": changes, "cursor": cursor}))
        else:
            import asyncio
            import inspect
            from dataclasses import replace

            from app.infrastructure.mcp.stdio import local_caller

            caller = replace(
                local_caller(args.actor if args.command == "users" else args.username, settings),
                origin="python",
            )
            if args.command == "users":
                import getpass

                from app.accounts import handlers

                if args.action == "list":
                    print(msgspec.json.encode(handlers.list_users(caller)).decode())
                elif args.action in {"grant", "revoke"}:
                    if not args.username or not args.mission:
                        parser.error("account and mission required")
                    (handlers.revoke if args.action == "revoke" else handlers.grant)(
                        caller,
                        handlers.GrantMission(
                            username=args.username, mission=args.mission, silo=args.silo
                        ),
                    )
                elif args.action == "add":
                    if not args.username:
                        parser.error("account required")
                    handlers.add(
                        caller,
                        handlers.NewUser(
                            username=args.username,
                            display_name=args.username,
                            password=None if args.email else getpass.getpass("Password: "),
                            email=args.email,
                        ),
                    )
                elif args.action == "approve":
                    if not args.username:
                        parser.error("email required")
                    approved = handlers.approve(
                        caller, handlers.AccessApproval(email=args.username)
                    )
                    print(msgspec.json.encode(approved).decode())
                elif args.action == "reset":
                    if not args.username:
                        parser.error("account required")
                    handlers.reset(
                        caller,
                        handlers.ResetPassword(
                            username=args.username, password=getpass.getpass("Password: ")
                        ),
                    )
                else:
                    if not args.username:
                        parser.error("account required")
                    (handlers.promote if args.action == "promote" else handlers.disable)(
                        caller, handlers.UserRef(username=args.username)
                    )
            else:
                from app.infrastructure.operations.registry import registry

                operation = registry.get(args.operation)
                if operation is None:
                    parser.error("unknown operation")
                if operation.input is None:
                    result = operation.function(caller)
                else:
                    result = operation.function(caller, msgspec.json.decode(args.arguments))
                if inspect.iscoroutine(result):
                    result = asyncio.run(result)
                print(msgspec.json.encode(result).decode())
    else:
        parser.print_help()
