"""Only fully readable current facet vectors contribute to mission comparison."""

import math

from app.cube import service, storage
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import InvalidInput, NotFound
from app.library import handlers as library

FACETS: list[library.Facet] = ["purpose", "design", "operations", "program", "cost_and_risk"]


def selected(request) -> list[library.Facet]:
    facets = request.facets if request.facets is not None else list(FACETS)
    if len(set(facets)) != len(facets):
        raise InvalidInput()
    return facets


def cosine(left, right):
    if left is None or right is None or left.vector is None or right.vector is None:
        return None
    a, b = left.vector, right.vector
    aa, bb = math.hypot(*a), math.hypot(*b)
    if not aa or not bb or not math.isfinite(aa) or not math.isfinite(bb):
        return None
    value = math.fsum((x / aa) * (y / bb) for x, y in zip(a, b, strict=True))
    return max(-1.0, min(1.0, value)) if math.isfinite(value) else None


def evidence(caller, missions, facets):
    return {
        (row.mission, row.facet): row
        for row in library.read_facets(
            caller, library.FacetRequest(missions=missions, facets=facets)
        )
    }


def similar(caller, request):
    from app.cube.handlers import SimilarMission, SimilarPage

    facets = selected(request)
    with unit_of_work(service.scope(caller), read_only=True) as session:
        source = storage.mission(session, caller, request.key)
        if source is None:
            raise NotFound()
        rows = [
            row
            for row in storage.similarity_missions(session, caller)
            if row["key"] != source["key"]
        ]
    values = evidence(caller, [source["key"], *[row["key"] for row in rows]], facets)
    result = []
    for row in rows:
        scores: dict[library.Facet, float | None] = {
            facet: cosine(values.get((source["key"], facet)), values.get((row["key"], facet)))
            for facet in facets
        }
        counted = [score for score in scores.values() if score is not None]
        comparable = len(counted) >= math.ceil(len(facets) / 2)
        score = math.fsum(counted) / len(counted) if comparable else None
        result.append(
            SimilarMission(
                mission=service.mission_shape(row),
                score=score,
                comparable=comparable,
                facet_scores=scores,
            )
        )
    result.sort(key=lambda row: (not row.comparable, -(row.score or 0), row.mission.key))
    return SimilarPage(missions=result[: request.limit], facets=facets)


def explain(caller, request):
    from app.cube.handlers import FacetExplanation, SimilarityExplanation

    facets = selected(request)
    with unit_of_work(service.scope(caller), read_only=True) as session:
        source = storage.mission(session, caller, request.key)
        other = storage.mission(session, caller, request.other)
        if source is None or other is None:
            raise NotFound()
    values = evidence(caller, [source["key"], other["key"]], facets)
    result = []
    for facet in facets:
        left = values.get((source["key"], facet))
        right = values.get((other["key"], facet))
        result.append(
            FacetExplanation(
                facet=facet,
                score=cosine(left, right),
                text=left.text if left else None,
                other_text=right.text if right else None,
                sources=left.sources if left else [],
                other_sources=right.sources if right else [],
            )
        )
    return SimilarityExplanation(facets=result)
