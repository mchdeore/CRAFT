"""An isolated prohibited decorator stack in either order."""

from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.operations import observe, operation


def invalid_stack(reverse: bool) -> None:
    def fixture(caller: Caller) -> None:
        return None

    if reverse:
        operation(PUBLIC)(observe("bad")(fixture))
    else:
        observe("bad")(operation(PUBLIC)(fixture))
