"""Attach owned immutable definitions to the existing native agent and MCP tools."""

from typing import Any

import msgspec
from agent_framework import Content, ContextProvider

from app.infrastructure.errors import ApiError, Conflict
from app.skills import service
from app.skills.handlers import Skill, SkillVersionRef

MAX_RUN_SKILLS = 21


def load(caller, references, allowed):
    """Resolve exact owned revisions while the ordinary operation scope is short lived."""
    if not isinstance(references, list) or len(references) > MAX_RUN_SKILLS:
        raise ApiError()
    try:
        checked = msgspec.convert(references, list[SkillVersionRef], strict=True)
    except (msgspec.ValidationError, TypeError) as exc:
        raise ApiError() from exc
    definitions = []
    for reference in checked:
        definition = Skill(**service.read(caller, reference.id, reference.revision))
        if definition.unavailable_tools or not set(definition.tool_names).issubset(allowed):
            raise Conflict()
        definitions.append(definition)
    return definitions


class SavedSkillsProvider(ContextProvider):
    def __init__(self, definitions: list[Skill], allowed=()):
        super().__init__("craft.saved_skills")
        self.allowed = frozenset(allowed)
        self.definitions: list[Skill] = []
        self.references: list[dict[str, Any]] = []
        for definition in definitions:
            self._remember(definition)

    def _remember(self, definition: Skill) -> None:
        reference = {"id": str(definition.id), "revision": definition.revision}
        if reference not in self.references:
            if len(self.references) >= MAX_RUN_SKILLS:
                raise ApiError()
            self.references.append(reference)
            self.definitions.append(definition)

    async def before_run(self, *, agent, session, context, state) -> None:
        if not self.definitions:
            return
        body = msgspec.json.encode(self.definitions).decode()
        body = body.replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")
        context.extend_instructions(
            self.source_id,
            "The following owned saved skills are selected or restored for this run. "
            "Follow their instructions using the actual registered CRAFT tools. "
            "The exact ID/revision identifies the definition; references grant no access "
            "or write approval. Existing tool authorization and approval still apply.\n"
            f"<craft_saved_skills>\n{body}\n</craft_saved_skills>",
        )

    def completed(self, invocation) -> None:
        """Record only a matching definition after the native tool invocation succeeds."""
        if invocation.function.name != "read_skill_version":
            return
        result = invocation.result
        if isinstance(result, list) and len(result) == 1:
            block = result[0]
            if isinstance(block, Content) and block.type == "text":
                result = block.text
        if not isinstance(result, str):
            return
        try:
            definition = msgspec.json.decode(result, type=Skill)
            reference = msgspec.convert(invocation.arguments, SkillVersionRef, strict=True)
        except (msgspec.DecodeError, msgspec.ValidationError, TypeError):
            return
        if definition.id != reference.id or definition.revision != reference.revision:
            raise ApiError()
        if definition.unavailable_tools or not set(definition.tool_names).issubset(self.allowed):
            return
        self._remember(definition)
