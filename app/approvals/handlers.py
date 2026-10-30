"""Requester-owned decision records; model tools cannot make human decisions."""

from typing import Annotated, Any, Literal
from uuid import UUID

import msgspec

from app.approvals import service
from app.infrastructure.access import SIGNED_IN, Caller, require
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape


def approved_request_id(caller: Caller, operation_name: str, value) -> UUID:
    """Return the receipt for the exact approved call executing a database effect."""
    return service.approved_request_id(caller, operation_name, value)


def approval_status_expression():
    """Share the canonical status projection with trusted metadata queries."""
    return service.status_expression()


def reject_pending(session, user, run) -> None:
    """Reject the run's pending approval in the caller's transaction: its paused run was
    cancelled, so nothing will resume it."""
    service.reject_pending(session, user, run)


def approved_preparation(caller: Caller, operation_name: str, value) -> tuple[UUID, object]:
    """Read only the server preparation bound to the current approved invocation."""
    require(caller, SIGNED_IN)
    return service.approved_preparation(caller, operation_name, value)


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


class GrantQuery(Shape):
    conversation_id: UUID | None = None


class GrantRef(Shape):
    id: UUID


class NotebookGrant(Shape):
    id: UUID
    conversation_id: UUID
    path: str
    approval_id: UUID
    created_at: str


@operation(SIGNED_IN, get="/notebook-grants")
def list_notebook_grants(caller: Caller, request: GrantQuery) -> list[NotebookGrant]:
    """List up to one hundred active notebook grants, optionally for one conversation."""
    return [NotebookGrant(**row) for row in service.list_grants(caller, request.conversation_id)]


@operation(SIGNED_IN, delete="/notebook-grants/{id}")
def revoke_notebook_grant(caller: Caller, request: GrantRef) -> None:
    """Revoke one notebook grant; the agent asks again before the next edit."""
    service.revoke_grant(caller, request.id)
