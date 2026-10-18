"""The skill editor uses authenticated HTTP; native skill use has a separate catalog."""

from typing import Annotated, ClassVar
from uuid import UUID

import msgspec

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import DATA, operation
from app.infrastructure.shapes import Shape
from app.skills import service

ToolName = Annotated[str, msgspec.Meta(pattern=r"^[a-z0-9_]{1,64}$")]


class SkillRef(Shape):
    id: UUID


class SkillVersionRef(SkillRef):
    revision: Annotated[int, msgspec.Meta(ge=1, le=2147483647)]


class SkillList(Shape):
    after: UUID | None = None
    limit: Annotated[int, msgspec.Meta(ge=1, le=100)] = 50


class SaveSkill(SkillRef):
    max_bytes: ClassVar[int] = 1024 * 1024
    expected_revision: Annotated[int, msgspec.Meta(ge=0, le=2147483646)]
    mutation_id: UUID
    name: Annotated[str, msgspec.Meta(min_length=1, max_length=200)]
    description: Annotated[str, msgspec.Meta(max_length=2000)]
    instructions: Annotated[str, msgspec.Meta(min_length=1, max_length=100000)]
    example_inputs: Annotated[
        list[Annotated[str, msgspec.Meta(max_length=2000)]], msgspec.Meta(max_length=20)
    ]
    tool_names: Annotated[list[ToolName], msgspec.Meta(max_length=100)]


class SkillSummary(Shape):
    id: UUID
    revision: int
    name: str
    description: str
    tool_names: list[str]
    unavailable_tools: list[str]
    created_at: str
    updated_at: str


class Skill(SkillSummary):
    instructions: str
    example_inputs: list[str]


class SkillCatalog(Shape):
    items: list[SkillSummary]
    next_cursor: UUID | None


@operation(SIGNED_IN, put="/skills/{id}", effects=DATA)
def save_skill(caller: Caller, request: SaveSkill) -> Skill:
    """Save an owned definition at its expected revision; replay identical mutations once."""
    return Skill(**service.save(caller, request))


@operation(SIGNED_IN, get="/skills")
def list_skills(caller: Caller, request: SkillList) -> SkillCatalog:
    """Page through current owned definitions; references reflect the current tool registry."""
    result = service.list_owned(caller, request.after, request.limit)
    return SkillCatalog(
        items=[SkillSummary(**row) for row in result["items"]], next_cursor=result["next_cursor"]
    )


@operation(SIGNED_IN, get="/skills/{id}")
def read_skill(caller: Caller, request: SkillRef) -> Skill:
    """Read the latest saved revision of one owned skill."""
    return Skill(**service.read(caller, request.id))


@operation(SIGNED_IN, get="/skills/{id}/versions")
def read_skill_version(caller: Caller, request: SkillVersionRef) -> Skill:
    """Read a particular immutable revision without substituting the current definition."""
    return Skill(**service.read(caller, request.id, request.revision))
