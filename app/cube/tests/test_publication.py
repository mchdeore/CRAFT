"""An approved batch publishes cited cells once, and evidence follows the cited source."""

from dataclasses import replace
from uuid import uuid4

import msgspec
import pytest
from sqlalchemy import func, select

from app.agent import service as agent_service
from app.agent.handlers import RunAgentInput
from app.approvals.handlers import (
    ApprovalRef,
    ApprovalRequest,
    Decision,
    decide_approval,
    read_approval,
    request_approval,
)
from app.cube.handlers import (
    PrepareValues,
    SubmitValues,
    ValueRef,
    get_value_evidence,
    prepare_values,
    submit_values,
)
from app.cube.tests.test_reads import cube as cube
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import Conflict, NotFound
from app.infrastructure.operations.pipeline import ApprovalRequired
from app.library.handlers import ChunkRange, read_chunks
from app.tests.support import rows
from app.tests.support.cube import cube_source as cube_source

POWER = b"# 2 Power\n\n| Element | Peak power (kW) |\n|---|---|\n| Mission total | 0.4 |\n"
REVISED = b"# 1 Budget\n\n| Parameter | Value | Unit |\n|---|---|---|\n| Peak power | 450 | W |\n"


@pytest.fixture
def run(cube):
    """The cube account's running chat turn, as the agent's tool calls see it."""
    owner, caller = cube["owner"], cube["caller"]
    conversations = m.metadata.tables["conversations"]
    with owner.begin() as connection:
        conversation = connection.execute(
            conversations.insert()
            .values(owner_id=caller.user_id, title="Generated cube review")
            .returning(conversations.c.id)
        ).scalar_one()
    identifier = uuid4()
    message = {"role": "user", "content": "Publish the power table."}
    agent_service.start(
        caller,
        RunAgentInput(thread_id=conversation, run_id=identifier, messages=[message]),
        "cube-tests",
    )
    yield replace(caller, origin="mcp", run_id=identifier, conversation_id=conversation)
    requests = m.metadata.tables["approval_requests"]
    events = m.metadata.tables["approval_events"]
    with owner.begin() as connection:
        ids = select(requests.c.id).where(requests.c.run_id == identifier)
        connection.execute(
            m.cube_values.delete().where(m.cube_values.c.approval_request_id.in_(ids))
        )
        connection.execute(events.delete().where(events.c.request_id.in_(ids)))
        connection.execute(requests.delete().where(requests.c.run_id == identifier))
        for name in ("messages", "agent_sessions", "agent_runs"):
            table = m.metadata.tables[name]
            connection.execute(table.delete().where(table.c.conversation_id == conversation))
        connection.execute(conversations.delete().where(conversations.c.id == conversation))


def reviewed(cube):
    """Write the power table, read its candidates and return them as one batch to submit."""
    path = f"missions/{cube['mission']}/engineering/st3-design/power.md"
    cube["provider"].write_content(path, POWER)
    rows.load(cube["owner"], cube["provider"])
    item = next(key for key, row in cube["provider"].items().items() if row["path"] == path)
    page = read_chunks(cube["caller"], ChunkRange(item=item, version=1, from_block=1, to_block=1))
    request = PrepareValues(handle=page.chunks[0].handle, valid_from="2026-02-01")
    prepared = prepare_values(cube["caller"], request)
    assert prepared.problems == []
    return path, SubmitValues(
        mission=cube["mission"],
        silo="engineering",
        stage="st3-design",
        valid_from="2026-02-01",
        candidates=prepared.candidates,
    )


def approve(cube, run, request):
    proposal = request_approval(
        run,
        ApprovalRequest(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            operation=submit_values.__operation__.name,
            arguments=msgspec.to_builtins(request),
            evidence=[],
            reasoning="Publish the reviewed power table.",
            retrieval_path=[],
            interrupt_id=uuid4().hex,
        ),
    )
    decide_approval(replace(cube["caller"], origin="http"), Decision(id=proposal.id, approve=True))
    return proposal.id


def published(cube, approval):
    with cube["owner"].connect() as connection:
        return connection.execute(
            select(func.count())
            .select_from(m.cube_values)
            .where(m.cube_values.c.approval_request_id == approval)
        ).scalar_one()


def test_an_approved_batch_publishes_its_cells_once(cube, run):
    _, request = reviewed(cube)
    # Outside an approved agent call, nothing is written.
    with pytest.raises(ApprovalRequired):
        submit_values(cube["caller"], request)
    approval = approve(cube, run, request)

    receipt = submit_values(run, request)

    assert receipt.approval_id == approval
    [identifier] = receipt.value_ids
    evidence = get_value_evidence(cube["caller"], ValueRef(id=identifier))
    value = evidence.value
    assert (value.mission, value.silo, value.stage, value.element) == (
        cube["mission"],
        "engineering",
        "st3-design",
        None,
    )
    assert (value.kind, value.low, value.high, value.unit) == ("exact", 400.0, 400.0, "W")
    assert (value.source_number, value.source_unit, value.valid_from) == ("0.4", "kW", "2026-02-01")
    assert value.approval_id == approval
    assert evidence.quote == "| Mission total | 0.4 |"
    assert evidence.conversion == "0.4 kW × 1000 = 400 W"
    assert evidence.citation_status == "current"
    # The approval is spent: the same call again needs a new one.
    with pytest.raises(ApprovalRequired):
        submit_values(run, request)
    assert published(cube, approval) == 1
    assert read_approval(cube["caller"], ApprovalRef(id=approval)).status == "executed"


def test_a_source_revised_after_approval_is_refused(cube, run):
    path, request = reviewed(cube)
    approval = approve(cube, run, request)
    cube["provider"].write_content(path, POWER.replace(b"0.4", b"0.5"))

    with pytest.raises(Conflict):
        submit_values(run, request)

    assert published(cube, approval) == 0
    assert read_approval(cube["caller"], ApprovalRef(id=approval)).status == "failed"


def test_value_evidence_follows_the_cited_source(cube):
    identifier = cube["add"](source_number="0.4", source_unit="kW")
    reference = ValueRef(id=identifier)

    evidence = get_value_evidence(cube["caller"], reference)
    assert (evidence.citation_status, evidence.quote, evidence.conversion) == (
        "current",
        "| Peak power | 400 | W |",
        "0.4 kW × 1000 = 400 W",
    )
    with pytest.raises(NotFound):
        get_value_evidence(cube["member"](), reference)

    cube["provider"].write_content(cube["path"], REVISED)
    rows.load(cube["owner"], cube["provider"])
    evidence = get_value_evidence(cube["caller"], reference)
    assert (evidence.citation_status, evidence.value.low, evidence.quote) == (
        "newer_version",
        400.0,
        "| Peak power | 400 | W |",
    )

    cube["provider"].delete_item(cube["item"])
    evidence = get_value_evidence(cube["caller"], reference)
    assert evidence.citation_status == "withdrawn"
    assert (evidence.value.withdrawn, evidence.value.kind, evidence.value.low) == (
        True,
        "unknown",
        None,
    )
    assert (evidence.quote, evidence.conversion) == ("", "No numeric source value.")
