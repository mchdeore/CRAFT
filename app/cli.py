"""The craft command. S0: --version. Later: seed, users, operations, call."""

import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="craft", description="CRAFT backend tools")
    parser.add_argument("--version", action="version", version=f"craft {version('craft')}")
    parser.parse_args(argv)
    parser.print_help()
