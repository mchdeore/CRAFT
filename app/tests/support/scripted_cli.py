"""Run the craft command line with the scripted model, for tests that start a subprocess."""

import sys

import pytest

from app.cli import main
from app.tests.support.model import install

if __name__ == "__main__":
    with pytest.MonkeyPatch.context() as patch:
        install(patch)
        main(sys.argv[1:])
