"""Model spend: one row of token counts per model response, priced by the settings, and the cap
that refuses a request once the spend since CRAFT_MODEL_BUDGET_SINCE reaches the budget.

Rows hold token counts, the role and the model name: never prompts, answers or keys."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    UUID,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings
from app.infrastructure.database.metadata import metadata
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import ApiError
from app.infrastructure.operations.context import current_call

model_usage = Table(
    "model_usage",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False),
    # The agent.model or agent.embedding step in call_log, which names the user and the run.
    Column("call_id", UUID, nullable=False),
    Column("role", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("input_tokens", Integer, nullable=False),  # includes the cached ones
    Column("cached_input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    CheckConstraint("role in ('main','check','embedding')", name="role"),
)
Index("ix_model_usage_at", model_usage.c.at)


class BudgetExhausted(ApiError):
    status, code, message = 429, "budget_exhausted", "Model budget reached."


def tokens(session: Session, since: datetime | None) -> dict[str, int]:
    """Tokens recorded since `since` (None: all of them)."""
    chat = model_usage.c.role != "embedding"
    query = select(
        func.coalesce(func.sum(model_usage.c.input_tokens).filter(chat), 0).label("input_tokens"),
        func.coalesce(func.sum(model_usage.c.cached_input_tokens), 0).label("cached_input_tokens"),
        func.coalesce(func.sum(model_usage.c.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(model_usage.c.input_tokens).filter(~chat), 0).label(
            "embedding_tokens"
        ),
    )
    if since is not None:
        query = query.where(model_usage.c.at >= since)
    return dict(session.execute(query).mappings().one())


def dollars(settings: Settings, counted: Mapping[str, int]) -> float:
    """Cached input is priced as input, so the meter never counts low."""
    return (
        counted["input_tokens"] * settings.model_price_input
        + counted["output_tokens"] * settings.model_price_output
        + counted["embedding_tokens"] * settings.embedding_price
    ) / 1_000_000


def check() -> None:
    """Before each model request: refuse once the recorded spend reaches the budget."""
    context = current_call.get()
    caller = context.caller if context is not None else None
    if caller is None or caller.sessions is None or caller.settings is None:
        return
    with unit_of_work(caller.sessions, read_only=True) as session:
        spent = dollars(caller.settings, tokens(session, caller.settings.model_budget_since))
    if spent >= caller.settings.model_budget_usd:
        raise BudgetExhausted()


def record(role: str, model: str, reported: Mapping[str, Any] | None) -> None:
    """One row for the token counts a response reported, linked to the current call step."""
    context = current_call.get()
    if reported is None or context is None or context.caller.sessions is None:
        return
    with unit_of_work(context.caller.sessions) as session:
        session.execute(
            model_usage.insert().values(
                at=datetime.now(UTC),
                call_id=context.call_id,
                role=role,
                model=model,
                input_tokens=reported.get("input_token_count") or 0,
                cached_input_tokens=reported.get("cache_read_input_token_count") or 0,
                output_tokens=reported.get("output_token_count") or 0,
            )
        )


def report(settings: Settings, sessions: sessionmaker[Session]) -> dict[str, Any]:
    """Tokens and dollars since CRAFT_MODEL_BUDGET_SINCE, for `craft usage`."""
    with unit_of_work(sessions, read_only=True) as session:
        counted = tokens(session, settings.model_budget_since)
    since = settings.model_budget_since
    return counted | {
        "since": since.isoformat() if since is not None else None,
        "spent_usd": round(dollars(settings, counted), 4),
        "budget_usd": settings.model_budget_usd,
    }
