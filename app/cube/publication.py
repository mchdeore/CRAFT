"""Source-validated factual publication inside the existing approved transaction."""

from dataclasses import dataclass
from datetime import date

import msgspec

from app.approvals.handlers import approved_preparation, approved_request_id
from app.cube import intake, storage
from app.cube.handlers import PrepareValues, SubmitValues, ValueReceipt, submit_values
from app.cube.service import prepare_values_from_chunk, scope
from app.infrastructure.access import Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, InvalidInput
from app.library.handlers import (
    ChunkRange,
    PreparedSource,
    prepare_source,
    read_chunks,
    validate_prepared_source,
)


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPublication:
    sources: tuple[PreparedSource, ...]


def prepare_submission(caller: Caller, request: SubmitValues) -> PreparedPublication:
    selected = [msgspec.json.encode(candidate) for candidate in request.candidates]
    if len(set(selected)) != len(selected):
        raise InvalidInput()
    sources = {}
    for candidate in request.candidates:
        handle = candidate.citation
        key = (handle.item, handle.version, handle.block)
        if key in sources:
            continue
        page = read_chunks(
            caller,
            ChunkRange(
                item=handle.item,
                version=handle.version,
                from_block=handle.block,
                to_block=handle.block,
            ),
        )
        if not page.chunks:
            raise Conflict()
        sources[key] = prepare_source(caller, page.chunks[0].handle)
    return PreparedPublication(sources=tuple(sources.values()))


def submit(caller, request):
    approval_id = approved_request_id(caller, submit_values.__operation__.name, request)
    prepared_id, preparation = approved_preparation(
        caller, submit_values.__operation__.name, request
    )
    if prepared_id != approval_id or type(preparation) is not PreparedPublication:
        raise Conflict()
    selected = [msgspec.json.encode(candidate) for candidate in request.candidates]
    if len(set(selected)) != len(selected):
        raise InvalidInput()
    with unit_of_work(scope(caller)) as session:
        dictionary = storage.dictionary(session)
        elements = storage.publication_elements(session, request.mission, request.silo)
        prepared = {}
        for source in preparation.sources:
            key = (source.item, source.version, source.block)
            if key in prepared:
                raise Conflict()
            validate_prepared_source(caller, session, source)
            chunk = source.chunk()
            candidates = prepare_values_from_chunk(
                caller,
                PrepareValues(
                    handle=chunk.handle,
                    mission=request.mission,
                    silo=request.silo,
                    stage=request.stage,
                    valid_from=request.valid_from,
                ),
                chunk,
                (source.mission, source.silo, source.stage),
            )
            if candidates.problems or (
                candidates.mission,
                candidates.silo,
                candidates.stage,
                candidates.valid_from,
            ) != (request.mission, request.silo, request.stage, request.valid_from):
                raise Conflict()
            prepared[key] = {
                msgspec.json.encode(value)
                for value in candidates.candidates
                if value.problem is None
            }
        rows = []
        for candidate, encoded in zip(request.candidates, selected, strict=True):
            handle = candidate.citation
            if encoded not in prepared.get((handle.item, handle.version, handle.block), set()):
                raise Conflict()
            parameter = dictionary.get(candidate.parameter)
            kind, number, unit, low, high, problem = intake.converted(
                "n/a" if candidate.kind == "not_applicable" else candidate.source_number,
                candidate.source_unit,
                parameter,
                exact=True,
            )
            if problem or (kind, number, unit) != (
                candidate.kind,
                candidate.source_number,
                candidate.source_unit,
            ):
                raise Conflict()
            element = elements.get(candidate.element) if candidate.element else None
            if candidate.element is not None and element is None:
                raise Conflict()
            rows.append(
                {
                    "mission_key": request.mission,
                    "silo": request.silo,
                    "stage": request.stage,
                    "element_id": element,
                    "parameter": candidate.parameter,
                    "kind": kind,
                    "low": low,
                    "high": high,
                    "source_number": number,
                    "source_unit": unit,
                    "valid_from": date.fromisoformat(request.valid_from),
                    "item": handle.item,
                    "version": handle.version,
                    "block": handle.block,
                    "locator": handle.locator,
                    "quote_sha256": bytes.fromhex(handle.quote_sha256),
                    "approval_request_id": approval_id,
                    "simulated": handle.simulated,
                }
            )
        return ValueReceipt(approval_id=approval_id, value_ids=storage.append_values(session, rows))
