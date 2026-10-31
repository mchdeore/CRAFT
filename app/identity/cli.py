"""`craft id`: serve craft-id, and manage its accounts and registered applications."""

import getpass
import json
import sys
from argparse import ArgumentParser


def engine(settings):
    from sqlalchemy import create_engine

    from app.infrastructure.database.engine import CONNECT_ARGS, database_url

    url = database_url(settings, "craft_id", settings.id_db_password)
    return create_engine(url, pool_pre_ping=True, connect_args=CONNECT_ARGS)


def run(parser: ArgumentParser, args) -> None:
    from sqlalchemy.orm import sessionmaker

    from app.identity import service
    from app.identity.settings import IdentitySettings
    from app.infrastructure.logging import configure_logging

    settings = IdentitySettings.from_env()
    if not settings.id_db_password:
        raise SystemExit("bad setting: id_db_password: invalid configuration")
    configure_logging(settings.log_level, sys.stderr)
    sessions = sessionmaker(engine(settings), expire_on_commit=False)
    if args.action == "serve":
        import uvicorn

        from app.identity.handlers import create_identity_app

        keys = service.Keys(settings.id_signing_key)
        app = create_identity_app(settings, sessions, keys)
        uvicorn.run(app, host="0.0.0.0", port=args.port, access_log=False)
    elif args.action == "clients" and args.verb == "add":
        if not args.name or not args.redirect_uri:
            parser.error("clients add needs a name and at least one --redirect-uri")
        identifier, secret = service.add_client(sessions, args.name, args.redirect_uri)
        # The secret is shown once, like an App Registration's; put it in CRAFT_OIDC_CLIENT_SECRET.
        print(json.dumps({"clientId": str(identifier), "clientSecret": secret}))
    elif args.action == "users" and args.verb == "add":
        if not args.name:
            parser.error("users add needs a username")
        password = getpass.getpass("Password: ")
        try:
            oid = service.add_account(
                sessions, args.name, args.display_name or args.name, args.email, password
            )
        except ValueError as error:  # too short
            parser.error(str(error))
        print(json.dumps({"oid": str(oid), "tid": str(service.TENANT)}))
    elif args.action == "users" and args.verb == "reset-password":
        try:
            if not args.name or not service.set_password(
                sessions, args.name, getpass.getpass("Password: ")
            ):
                parser.error("unknown account")
        except ValueError as error:  # too short
            parser.error(str(error))
    elif args.action == "users" and args.verb == "disable":
        if not args.name or not service.disable_account(sessions, args.name):
            parser.error("unknown account")
    else:
        parser.error("unknown id command")
