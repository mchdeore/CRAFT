"""The worker heartbeat file and its health check, using the standard library only.

A running worker touches `FILE` every few seconds (`app.infrastructure.queue`). Container
probes check it by running this file by path:

    python -I -S /app/app/infrastructure/heartbeat.py

That starts a bare interpreter and imports nothing from `app`. `craft worker --check` would
first import FastAPI, SQLAlchemy and Procrastinate (through the `app` package and the queue),
many times the cost of a bare interpreter, and far slower on a busy machine. Exec probes
outlive their timeout, so slow probes pile up and add to the load that made them slow. `-I`
keeps this file's folder off `sys.path`, so a sibling module such as `logging.py` here can't
shadow the standard library, and `-S` skips `site` and the virtualenv's `.pth` files. Keep
this module free of imports outside the standard library; a test checks that.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

FILE = Path(tempfile.gettempdir()) / "craft-worker-heartbeat"
# A worker whose heartbeat is older than this is unhealthy.
HEALTHY_SECONDS = 60.0


def healthy() -> bool:
    """This container's worker touched the heartbeat file within `HEALTHY_SECONDS`."""
    try:
        return time.time() - os.stat(FILE).st_mtime < HEALTHY_SECONDS
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(0 if healthy() else 1)
