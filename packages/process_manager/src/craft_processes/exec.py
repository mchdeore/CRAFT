"""Preserve argv literally across Supervisor's INI parsing, without a shell."""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    arguments = json.loads(Path(sys.argv[1]).read_text())
    if (
        not isinstance(arguments, list)
        or not arguments
        or not all(isinstance(value, str) and "\x00" not in value for value in arguments)
    ):
        raise SystemExit(2)
    os.execv(arguments[0], arguments)


if __name__ == "__main__":
    main()
