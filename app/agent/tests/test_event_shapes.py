"""Supported framework events remain lossless across the strict wire shape."""

import msgspec
import pytest
from ag_ui.core import ReasoningEncryptedValueEvent

from app.agent.handlers import AGUIEvent


@pytest.mark.parametrize("subtype", ["message", "tool-call"])
def test_framework_encrypted_reasoning_event_round_trip(subtype):
    event = ReasoningEncryptedValueEvent(
        subtype=subtype,
        entity_id="generated-entity",
        encrypted_value="opaque-generated-fixture",
        metadata={"generated": True},
        subagent_run_id="generated-subagent",
    ).model_dump(mode="json", by_alias=True, exclude_none=True)

    converted = msgspec.convert(event, AGUIEvent)

    assert msgspec.json.decode(msgspec.json.encode(converted)) == event


def test_event_shape_still_rejects_unknown_fields():
    with pytest.raises(msgspec.ValidationError, match="unknown field"):
        msgspec.convert({"type": "RUN_STARTED", "unsupportedField": "generated"}, AGUIEvent)


@pytest.mark.parametrize("field,value", [("subtype", "unsupported"), ("encryptedValue", 12)])
def test_encrypted_event_fields_still_validate_types(field, value):
    event = {
        "type": "REASONING_ENCRYPTED_VALUE",
        "subtype": "message",
        "entityId": "generated",
        "encryptedValue": "opaque",
    }
    event[field] = value
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert(event, AGUIEvent)
