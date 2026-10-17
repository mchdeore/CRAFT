"""Requester-owned decision records; model tools cannot make human decisions."""

from typing import Annotated, Any, Literal
from uuid import UUID

import msgspec

from app.approvals import service
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape

Status = Literal["pending", "approved", "rejected", "executed", "failed"]


class ApprovalRef(Shape):
    id: UUID


class ApprovalQuery(Shape):
    status: Status | None = None


class Approval(Shape):
    id: UUID
    conversation_id: UUID
    run_id: UUID
    tool: str
    summary: str
    status: Status
    requested_at: str


class ApprovalEvent(Shape, omit_defaults=True):
    kind: Literal["requested", "approved", "rejected", "executed", "failed"]
    actor_id: UUID | None
    at: str
    note: str | None = None


class ApprovalDetail(Approval):
    arguments: dict[str, Any]
    evidence: list[dict[str, Any]]
    reasoning: str
    retrieval_path: list[dict[str, Any]]
    events: list[ApprovalEvent]


class Decision(Shape):
    id: UUID
    approve: bool
    note: Annotated[str, msgspec.Meta(max_length=2000)] | None = None


class ApprovalRequest(Shape):
    run_id: UUID
    conversation_id: UUID
    operation: Annotated[str, msgspec.Meta(min_length=1, max_length=200)]
    arguments: dict[str, Any]
    evidence: list[dict[str, Any]]
    reasoning: Annotated[str, msgspec.Meta(min_length=1, max_length=4000)]
    retrieval_path: list[dict[str, Any]]
    interrupt_id: Annotated[str, msgspec.Meta(min_length=1, max_length=200)]


@operation(SIGNED_IN)
def request_approval(caller: Caller, request: ApprovalRequest) -> Approval:
    """Freeze a server-created request for one owned active run."""
    return Approval(**service.request(caller, request))


@operation(SIGNED_IN, get="/approvals")
def list_approvals(caller: Caller, request: ApprovalQuery) -> list[Approval]:
    """List up to one hundred requester-owned decision records."""
    return [Approval(**row) for row in service.list_owned(caller, request.status)]


@operation(SIGNED_IN, get="/approvals/{id}")
def read_approval(caller: Caller, request: ApprovalRef) -> ApprovalDetail:
    """Read the frozen action, evidence and event history for one owned request."""
    data = service.read(caller, request.id)
    data["events"] = [ApprovalEvent(**event) for event in data["events"]]
    return ApprovalDetail(**data)


@operation(SIGNED_IN, post="/approvals/{id}/decision")
def decide_approval(caller: Caller, request: Decision) -> Approval:
    """Record an authenticated requester decision once through HTTP."""
    return Approval(**service.decide(caller, request))
