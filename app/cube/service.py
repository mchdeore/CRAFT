"""Unit-converted cube reads in service-selected short transactions."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal

import msgspec
from sqlalchemy import select

from app.cube import state, storage, units
from app.infrastructure import access
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import InvalidInput, NotFound
from app.library import handlers as library


def list_missions(caller, request):
    if caller.sessions is None:
        raise NotFound()
    with unit_of_work(caller.sessions, read_only=True) as session:
        rows = storage.list_missions(session, caller, request.query, request.limit)
    return [mission_shape(row) for row in rows]


def change_stage(settings, mission: str, stage: str) -> None:
    sessions, engine = make_owner_sessions(settings)
    try:
        with unit_of_work(sessions) as session:
            if not storage.set_stage(session, mission, stage):
                raise NotFound()
    finally:
        engine.dispose()


def scope(caller):
    if caller.sessions is None or caller.settings is None:
        raise NotFound()
    return caller.sessions


def calendar(request):
    now = access.now()
    try:
        if request.at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", request.at):
            raise ValueError()
        at = date.fromisoformat(request.at) if request.at else now.date()
        known = (
            datetime.fromisoformat(request.known_at.replace("Z", "+00:00"))
            if request.known_at
            else now
        )
        if known.tzinfo is None:
            raise ValueError()
        return at, known.astimezone(UTC)
    except ValueError:
        raise InvalidInput() from None


def mission_shape(row):
    from app.cube.handlers import Mission

    return msgspec.convert(
        {
            "key": row["key"],
            "title": row["title"],
            "currentStage": row["current_stage"],
            "aliases": row["aliases"],
            "simulated": row["simulated"],
        },
        Mission,
    )


def value_shape(row, dictionary):
    from app.cube.handlers import CubeValue

    return msgspec.convert(
        {
            "id": row["id"],
            "mission": row["mission_key"],
            "silo": row["silo"],
            "stage": row["stage"],
            "element": row["element_id"],
            "parameter": row["parameter"],
            "kind": row["kind"],
            "low": float(row["low"]) if row["low"] is not None else None,
            "high": float(row["high"]) if row["high"] is not None else None,
            "unit": dictionary[row["parameter"]]["unit"],
            "sourceNumber": row["source_number"],
            "sourceUnit": row["source_unit"],
            "validFrom": row["valid_from"].isoformat(),
            "recordedAt": row["recorded_at"].isoformat(),
            "citation": {
                "item": row["item"],
                "version": row["version"],
                "block": row["block"],
                "locator": row["locator"],
                "quoteSha256": bytes(row["quote_sha256"]).hex(),
                "simulated": row["simulated"],
            },
            "withdrawn": row["withdrawn"],
            "approvalId": row["approval_request_id"],
            "simulated": row["simulated"],
        },
        CubeValue,
    )


def list_parameters(caller):
    from app.cube.handlers import Parameter, Unit

    with unit_of_work(scope(caller), read_only=True) as session:
        dictionary = storage.dictionary(session)
    return [
        Parameter(
            key=key,
            label=row["label"],
            unit=row["unit"],
            min=float(row["min_value"]) if row["min_value"] is not None else None,
            max=float(row["max_value"]) if row["max_value"] is not None else None,
            whole=row["whole"],
            units=[
                Unit(unit=unit, multiplier=str(multiplier), offset=str(offset))
                for unit, (multiplier, offset) in row["conversions"].items()
            ],
        )
        for key, row in sorted(dictionary.items())
    ]


def prepare_values(caller, request):
    document = library.get_document(
        caller,
        library.DocumentRef(item=request.handle.item, version=request.handle.version),
    )
    page = library.read_chunks(
        caller,
        library.ChunkRange(
            item=request.handle.item,
            version=request.handle.version,
            from_block=request.handle.block,
            to_block=request.handle.block,
        ),
    )
    if not page.chunks or page.chunks[0].handle != request.handle:
        raise NotFound()
    return prepare_values_from_chunk(
        caller,
        request,
        page.chunks[0],
        (document.tags.mission, document.tags.silo, document.tags.stage),
    )


def prepare_values_from_chunk(caller, request, chunk, source_scope):
    """Derive candidates from a checked chunk and the current intake dictionary."""
    from app.cube import intake

    source_mission, source_silo, source_stage = source_scope
    target = request.mission if request.mission is not None else source_mission
    silo = request.silo if request.silo is not None else source_silo
    stage = request.stage if request.stage is not None else source_stage
    if request.valid_from is not None:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", request.valid_from):
                raise ValueError()
            date.fromisoformat(request.valid_from)
        except ValueError:
            raise InvalidInput() from None
    with unit_of_work(scope(caller), read_only=True) as session:
        storage.scope(session, silo, stage)
        mission = storage.mission(session, caller, target) if target else None
        if request.mission is not None and mission is None:
            raise NotFound()
        if mission and not storage.intake_grant(session, caller, mission["key"], silo):
            raise NotFound()
        target = mission["key"] if mission else None
        if stage == "current":
            stage = mission["current_stage"] if mission else None
        dictionary, synonyms, names = storage.intake_dictionary(session, caller, target, silo)
    return intake.prepare(
        chunk,
        dictionary,
        synonyms,
        names,
        target,
        silo,
        stage,
        request.valid_from,
    )


def submit_values(caller, request):
    from app.cube import publication

    return publication.submit(caller, request)


def find_missions(caller, request):
    from app.cube.handlers import FoundMissions, MatchedMission

    sessions = scope(caller)
    at, known = calendar(request)
    with unit_of_work(sessions, read_only=True) as session:
        dictionary = storage.dictionary(session)
        storage.scope(session, request.silo, request.stage)
    conditions = []
    for condition in request.conditions:
        if condition.parameter not in dictionary:
            raise InvalidInput()
        conversions = dictionary[condition.parameter]["conversions"]
        low = units.convert(condition.value, condition.unit, conversions)
        if condition.op == "between":
            if condition.high is None:
                raise InvalidInput()
            high = units.convert(condition.high, condition.unit, conversions)
            if high < low:
                raise InvalidInput()
        else:
            if condition.high is not None:
                raise InvalidInput()
            high = None
        conditions.append((condition.parameter, condition.op, low, high))
    sources = state.sources(caller)
    active = [key for key, row in sources.items() if row["deleted_at"] is None]
    with unit_of_work(sessions, read_only=True) as session:
        found = storage.find(
            session,
            caller,
            at,
            known,
            {"silo": request.silo, "stage": request.stage},
            conditions,
            active,
            request.limit,
        )
    return FoundMissions(
        missions=[
            MatchedMission(
                mission=mission_shape(mission),
                matched=[
                    value_shape(row, dictionary) for row in state.with_sources(values, sources)
                ],
            )
            for mission, values in found[: request.limit]
        ],
        at=at.isoformat(),
        known_at=known.isoformat(),
        truncated=len(found) > request.limit,
    )


def get_mission(caller, request):
    from app.cube.handlers import MissionState

    sessions = scope(caller)
    at, known = calendar(request)
    with unit_of_work(sessions, read_only=True) as session:
        mission = storage.mission(session, caller, request.key)
        if mission is None:
            raise NotFound()
        storage.scope(session, request.silo, request.stage)
        dictionary = storage.dictionary(session)
        rows = state.latest(
            session,
            caller,
            at,
            known,
            silo=request.silo,
            stage=request.stage,
            keys=[mission["key"]],
        )
    visible = state.with_sources(rows, state.sources(caller))
    return MissionState(
        mission=mission_shape(mission),
        values=[value_shape(row, dictionary) for row in visible],
        gaps=sorted(set(dictionary) - {row["parameter"] for row in visible}),
        at=at.isoformat(),
        known_at=known.isoformat(),
    )


def comparable(candidates, element_names):
    selected = []
    for rows in candidates:
        totals = [row for row in rows if row["element_id"] is None]
        selected.append(totals if totals else rows)
    signatures = []
    for rows in selected:
        if len(rows) == 1:
            row = rows[0]
            name = (
                element_names.get(row["element_id"], str(row["element_id"]))
                if row["element_id"]
                else None
            )
            signatures.append((row["silo"], name))
    target = (
        signatures[0]
        if signatures and all(value == signatures[0] for value in signatures)
        else None
    )
    result = []
    for rows in selected:
        if len(rows) != 1 or target is None:
            result.append(None)
            continue
        row = rows[0]
        name = (
            element_names.get(row["element_id"], str(row["element_id"]))
            if row["element_id"]
            else None
        )
        result.append(row if (row["silo"], name) == target else None)
    return result


def compare_missions(caller, request):
    from app.cube.handlers import Comparison, ComparisonRow

    sessions = scope(caller)
    at, known = calendar(request)
    with unit_of_work(sessions, read_only=True) as session:
        missions = []
        for key in request.keys:
            resolved = storage.mission(session, caller, key)
            if resolved is None:
                raise NotFound()
            missions.append(resolved)
        keys = [row["key"] for row in missions]
        if len(set(keys)) != len(keys) or len(set(request.parameters)) != len(request.parameters):
            raise InvalidInput()
        storage.scope(session, request.silo, request.stage)
        dictionary = storage.dictionary(session)
        if any(key not in dictionary for key in request.parameters):
            raise InvalidInput()
        rows = state.latest(
            session, caller, at, known, silo=request.silo, stage=request.stage, keys=keys
        )
        elements = {
            row.id: row.name for row in session.execute(select(m.elements.c.id, m.elements.c.name))
        }
    visible = state.with_sources(rows, state.sources(caller))
    columns = [
        comparable(
            [
                [
                    row
                    for row in visible
                    if row["mission_key"] == key and row["parameter"] == parameter
                ]
                for key in keys
            ],
            elements,
        )
        for parameter in request.parameters
    ]
    return Comparison(
        parameters=request.parameters,
        rows=[
            ComparisonRow(
                mission=mission_shape(mission),
                values=[
                    value_shape(column[index], dictionary) if column[index] is not None else None
                    for column in columns
                ],
            )
            for index, mission in enumerate(missions)
        ],
        at=at.isoformat(),
        known_at=known.isoformat(),
    )


def decimal_text(value):
    return (
        format(Decimal(value), "f").rstrip("0").rstrip(".")
        if "." in format(Decimal(value), "f")
        else format(Decimal(value), "f")
    )


def label_values(caller, request):
    from app.cube.handlers import ValueLabel

    with unit_of_work(scope(caller), read_only=True) as session:
        rows = storage.value_labels(session, caller, request.ids)
    return [ValueLabel(id=row["id"], label=row["label"]) for row in rows]


def get_value_evidence(caller, request):
    from app.cube.handlers import ValueEvidence

    with unit_of_work(scope(caller), read_only=True) as session:
        row = storage.value(session, caller, request.id)
        if row is None:
            raise NotFound()
        dictionary = storage.dictionary(session)
    selected = state.with_sources([row], state.sources(caller))[0]
    value = value_shape(selected, dictionary)
    status = library.citation_status(caller, value.citation)
    if value.withdrawn or status == "withdrawn":
        status = "withdrawn"
        value = msgspec.structs.replace(value, kind="unknown", low=None, high=None, withdrawn=True)
        quote = ""
    else:
        quote = library.citation_quote(caller, value.citation)
    conversion = "No numeric source value."
    if (
        not value.withdrawn
        and row["source_number"] is not None
        and row["source_unit"] in dictionary[row["parameter"]]["conversions"]
    ):
        multiplier, offset = dictionary[row["parameter"]]["conversions"][row["source_unit"]]
        adjustment = (" + " + decimal_text(offset)) if offset else ""
        conversion = (
            (
                f"{row['source_number']} {row['source_unit']} × "
                f"{decimal_text(multiplier)}{adjustment} = "
                f"{decimal_text(row['low'])} {dictionary[row['parameter']]['unit']}"
            )
            if row["low"] is not None
            else conversion
        )
    return ValueEvidence(value=value, quote=quote, conversion=conversion, citation_status=status)
