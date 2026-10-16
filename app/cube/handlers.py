"""Mission read operations and the local stage maintenance entry point."""

from typing import Annotated

import msgspec

from app.cube import service
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape


class Mission(Shape):
    key: str
    title: str
    current_stage: str
    aliases: list[str]
    simulated: bool


class ListMissions(Shape):
    query: str = ""
    limit: Annotated[int, msgspec.Meta(ge=1, le=100)] = 20


@operation(SIGNED_IN, get="/cube/missions", tool=True)
def list_missions(caller: Caller, request: ListMissions) -> list[Mission]:
    """Resolve readable mission names and aliases. Use before cube reads; for example sim-01."""
    return service.list_missions(caller, request)


def change_stage(settings: Settings, mission: str, stage: str) -> None:
    """Maintain one mission's lifecycle stage through the local owner command."""
    service.change_stage(settings, mission, stage)
