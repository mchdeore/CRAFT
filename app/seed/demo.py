"""Reproducible fictional sources, indexed citations and seeded mission values."""

import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.config.settings import Settings
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.paths import ROOT
from app.infrastructure.provider import Provider, get_provider
from app.library.handlers import convert_source as convert
from app.library.handlers import index_provider
from app.seed import files
from app.seed.base import set_demo_passwords
from app.seed.vocabulary import ELEMENTS, FAMILIES, PARAMETERS, narrative

CLOCK = "2026-01-01T00:00:00+00:00"
RECIPE = "demo-v1"
ACCEPTED = datetime(2026, 10, 1, tzinfo=UTC)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def active_files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts)
    }


def item_at(provider: Provider, path: str) -> dict:
    return next(
        row
        for row in provider.items().values()
        if row["path"] == path and row["deleted_at"] is None
    )


def handle(item: str, version: int, block) -> dict:
    return {
        "item": item,
        "version": version,
        "block": block.block,
        "locator": block.locator,
        "quoteSha256": digest(block.text.encode()),
        "simulated": True,
    }


def budgets(
    number: int, values: dict, french: bool, later: bool = False
) -> tuple[list, list, list, list]:
    mass_unit = "g" if french else "kg"
    volume_unit = "L" if french else "m3"
    power_unit = "kW" if french else "W"
    orbit_unit = "m" if french else "km"
    mass_headers = [
        "Element",
        f"{'Masse sèche' if french else 'Dry mass'} ({mass_unit})",
        f"{'Volume utile' if french else 'Payload volume'} ({volume_unit})",
        "Design life",
    ]
    power_headers = [
        "Element",
        f"{'Puissance crête' if french else 'Peak power'} ({power_unit})",
        f"{'Altitude orbitale' if french else 'Orbit altitude'} ({orbit_unit})",
        f"{'Maturité' if french else 'Readiness'} (level)",
        f"{'Durée de vie' if french else 'Design life'} (year)",
        "Calculated total (W)",
    ]
    mass = Decimal(str(values["mass"])) * (1000 if french else 1)
    volume = Decimal(str(values["volume"])) * (1000 if french else 1)
    power = (
        Decimal(str(values["power"])) * (Decimal("1.1") if later else 1) / (1000 if french else 1)
    )
    orbit = Decimal(str(values["orbit"])) * (1000 if french else 1)
    mass_rows = [["Mission total", float(mass), float(volume), values["life"]]]
    power_rows = [["Mission total", float(power), float(orbit), "TBD", values["life"], "=1/0"]]
    for name, fraction in zip(
        ELEMENTS, [Decimal("0.2"), Decimal("0.35"), Decimal("0.45")], strict=True
    ):
        mass_rows.append([name, float(mass * fraction), float(volume * fraction), values["life"]])
        power_rows.append(
            [name, float(power * fraction), float(orbit), 3 + number % 5, values["life"], None]
        )
    return mass_headers, mass_rows, power_headers, power_rows


def source_values(
    provider: Provider, mission: dict, paths: tuple[str, str], generation: int, seed: int
) -> list[dict]:
    definitions = {row[0]: row for row in PARAMETERS}
    records = []
    for path, columns in [
        (paths[0], [("mass_dry", 1), ("payload_volume", 2)]),
        (
            paths[1],
            [("power_peak", 1), ("orbit_altitude", 2), ("readiness", 3), ("design_life", 4)],
        ),
    ]:
        item = item_at(provider, path)
        versions = item["versions"][-2:] if path == paths[1] else item["versions"][-1:]
        for version in versions:
            content = provider.read_content(item["item"], version["version"])
            blocks = convert(content, path)
            block = next(
                b for b in blocks if b.kind == "table" and "Element" in b.text.splitlines()[0]
            )
            row = block.text.splitlines()[2]
            cells = [value.strip() for value in row.strip("|").split("|")]
            headers = [value.strip() for value in block.text.splitlines()[0].strip("|").split("|")]
            date = "2026-03-01" if path == paths[1] and version == versions[-1] else "2026-01-01"
            for parameter, column in columns:
                unit = headers[column].rsplit("(", 1)[1].rstrip(")")
                raw = cells[column]
                kind = "unknown" if raw == "TBD" else "exact"
                converted = (
                    None
                    if kind == "unknown"
                    else Decimal(raw) * Decimal(definitions[parameter][6][unit])
                )
                citation = handle(item["item"], version["version"], block)
                citation["locator"] += ";row=5"
                citation["quoteSha256"] = digest(row.encode())
                identifier = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"craft-seed:{seed}:{generation}:{mission['key']}:{parameter}:{date}:{item['item']}:{version['version']}",
                    )
                )
                records.append(
                    {
                        "id": identifier,
                        "mission": mission["key"],
                        "parameter": parameter,
                        "silo": "engineering",
                        "stage": mission["currentStage"],
                        "element": None,
                        "date": date,
                        "validFrom": date,
                        "kind": kind,
                        "low": float(converted) if converted is not None else None,
                        "high": float(converted) if converted is not None else None,
                        "unit": definitions[parameter][2],
                        "sourceNumber": raw if kind == "exact" else None,
                        "sourceUnit": unit,
                        "recordedAt": (
                            ACCEPTED + timedelta(hours=generation, minutes=version["version"])
                        ).isoformat(),
                        "citation": citation,
                        "rowIndex": 2,
                        "withdrawn": False,
                        "storedKind": kind,
                        "storedLow": float(converted) if converted is not None else None,
                        "storedHigh": float(converted) if converted is not None else None,
                    }
                )
    return records


def prepare(root: Path, seed: int) -> tuple[dict, list[str]]:
    root.mkdir(parents=True, exist_ok=True)
    truth_path = root / ".provider/ground-truth.json"
    previous = json.loads(truth_path.read_text()) if truth_path.exists() else None
    if (
        previous
        and previous["seed"] == seed
        and previous.get("recipe") == RECIPE
        and previous.get("fingerprint") == active_files(root)
    ):
        return previous, []
    generation = previous.get("generation", 0) + 1 if previous else 1
    provider = get_provider(root, clock=CLOCK)
    rng = random.Random(seed)
    truth = {
        "seed": seed,
        "recipe": RECIPE,
        "generation": generation,
        "missions": [],
        "families": [],
        "questions": [],
        "versions": [],
        "moves": [],
        "deletes": [],
        "duplicates": [],
        "values": [],
    }
    for family in range(4):
        truth["families"].append(
            {
                "key": FAMILIES[family][0],
                "missions": [f"msn-sim-{family * 3 + n:02}" for n in range(1, 4)],
            }
        )
    references = {
        "reference/glossary.md": (
            f"# Simulated glossary\n\nScenario {seed}. "
            "Allocation means a budgeted limit. Verification means an observed check.\n"
        ),
        "reference/units.md": (
            f"# Simulated units\n\nScenario {seed}. "
            "Mass uses kg; power uses W; altitude uses km. Units accompany source numbers.\n"
        ),
    }
    for path, content in references.items():
        target = provider.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    planned = []
    for number in range(1, 13):
        family = (number - 1) // 3
        family_key, theme, focus, mass, power, orbit = FAMILIES[family]
        title = f"Simulated mission {number:02}"
        stage = ["st3-design", "st2-definition", "st4-build"][(number - 1) % 3]
        mission = {
            "key": f"msn-sim-{number:02}",
            "title": title,
            "family": family_key,
            "aliases": [f"sim-{number:02}"],
            "currentStage": stage,
            "simulated": True,
        }
        truth["missions"].append(mission)
        values = {
            "mass": mass + rng.randrange(25),
            "power": power + rng.randrange(90),
            "orbit": orbit + rng.randrange(45),
            "volume": round(0.15 + family * 0.4 + rng.random() / 10, 3),
            "life": 3 + family * 2 + number % 3,
            "margin": 5 + rng.randrange(6),
            "rate": 2 + family * 4 + number % 3,
        }
        facts = [
            f"The radiator margin for {title} is {values['margin']} K.",
            f"The design life for {title} is {values['life']} years.",
            f"The downlink rate for {title} is {values['rate']} Mbps.",
            f"The review outcome for {title} is conditional acceptance with interface closure.",
        ]
        questions = [
            f"What radiator margin is recorded for {title}?",
            f"What design life is recorded for {title}?",
            f"What downlink rate is recorded for {title}?",
            f"What review outcome is recorded for {title}?",
        ]
        base = f"missions/{mission['key']}"
        specs = [
            ("requirements/st2-definition/mission-overview.md", "md", 0),
            (f"engineering/{stage}/thermal-design.docx", "docx", 1),
            ("launch/st6-operation/downlink-plan.pdf", "pdf", 2),
            (f"reviews/{stage}/review-brief.pptx", "pptx", 3),
            ("quality/st5-verification/verification-notes.html", "html", None),
            (f"engineering/{stage}/interface-guide.txt", "txt", None),
            ("requirements/st1-concept/payload-rationale.docx", "docx", None),
        ]
        fact_paths = []
        for relative, format, fact in specs:
            path = base + "/" + relative
            if previous:
                moved = next((m for m in previous["moves"] if m["from"] == path), None)
                if moved:
                    path = moved["to"]
            paragraphs = narrative(title, theme, focus, seed)
            if fact is not None:
                paragraphs.insert(1, facts[fact])
                fact_paths.append((fact, path))
            target = provider.path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                files.document(
                    title + " / " + relative.split("/")[-1].split(".")[0], paragraphs, format
                )
            )
        mass_path = base + f"/engineering/{stage}/mass-budget.xlsx"
        power_path = base + f"/engineering/{stage}/power-budget.xlsx"
        mh, mr, ph, pr = budgets(number, values, number % 2 == 0)
        for path, headers, rows in [(mass_path, mh, mr), (power_path, ph, pr)]:
            target = provider.path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files.spreadsheet(title, "2026-01-01", headers, rows))
        cost_path = provider.path(base + "/budget/st2-definition/cost-model.csv")
        cost_path.parent.mkdir(parents=True, exist_ok=True)
        cost_path.write_bytes(files.comma_table(title, seed))
        planned.append((mission, values, fact_paths, questions, facts, mass_path, power_path))
    provider.scan()
    for mission, values, fact_paths, questions, facts, mass_path, power_path in planned:
        original = item_at(provider, power_path)
        before = original["versions"][-1]["version"]
        number = int(mission["key"][-2:])
        _, _, headers, rows = budgets(number, values, number % 2 == 0, later=True)
        provider.write_content(
            power_path, files.spreadsheet(mission["title"], "2026-03-01", headers, rows)
        )
        latest = item_at(provider, power_path)
        truth["versions"].append(
            {
                "item": latest["item"],
                "fromVersion": before,
                "toVersion": latest["versions"][-1]["version"],
                "parameter": "power_peak",
                "validFrom": "2026-03-01",
            }
        )
        for index, path in fact_paths:
            item = item_at(provider, path)
            version = item["versions"][-1]["version"]
            answers = [
                handle(item["item"], version, b)
                for b in convert(provider.read_content(item["item"], version), path)
                if facts[index] in " ".join(b.text.split())
            ]
            if not answers:
                raise ValueError("seed fact has no source block")
            truth["questions"].append(
                {
                    "id": f"q-{number:02}-{index + 1}",
                    "question": questions[index],
                    "mission": mission["key"],
                    "expectedText": facts[index],
                    "answers": answers,
                }
            )
        truth["values"].extend(
            source_values(provider, mission, (mass_path, power_path), generation, seed)
        )
        if number in {1, 4, 7}:
            duplicate = power_path.replace("power-budget.xlsx", "power-budget-copy.xlsx")
            provider.write_content(
                duplicate,
                files.spreadsheet(mission["title"], "2026-03-01", headers, rows, copy=True),
            )
            truth["duplicates"].append(
                {"item": item_at(provider, duplicate)["item"], "original": latest["item"]}
            )
    if previous:
        truth["moves"] = previous["moves"]
    else:
        for number in [5, 9]:
            mission = truth["missions"][number - 1]
            old = f"missions/{mission['key']}/reviews/{mission['currentStage']}/review-brief.pptx"
            new = old.replace(
                mission["currentStage"],
                "st4-build" if mission["currentStage"] != "st4-build" else "st5-verification",
            )
            item = item_at(provider, old)["item"]
            target = provider.path(new)
            target.parent.mkdir(parents=True, exist_ok=True)
            provider.path(old).rename(target)
            provider.scan()
            truth["moves"].append({"item": item, "from": old, "to": new})
    pending = [
        row["item"]
        for row in truth["versions"]
        if int(provider.get_item(row["item"])["mission_key"][-2:]) % 4 == 0
    ]
    return truth, pending


def finish(root: Path, truth: dict, pending: list[str]) -> dict:
    provider = get_provider(root, clock=CLOCK)
    for item in pending:
        provider.delete_item(item)
    truth["deletes"] = [{"item": item} for item in pending] or truth["deletes"]
    deleted = {row["item"] for row in truth["deletes"]}
    for value in truth["values"]:
        if value["citation"]["item"] in deleted:
            value.update(withdrawn=True, kind="unknown", low=None, high=None)
    truth["documents"] = len(provider.items())
    truth["fingerprint"] = active_files(root)
    target = root / ".provider/ground-truth.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(truth, sort_keys=True, indent=2) + "\n")
    return truth


def generate(root: Path, seed: int = 1) -> dict:
    """Generate the source and ground-truth records without database work."""
    truth, pending = prepare(root, seed)
    return finish(root, truth, pending)


def load_cube(settings: Settings, truth: dict) -> None:
    sessions, engine = make_owner_sessions(settings)
    try:
        with unit_of_work(sessions) as session:
            users = m.metadata.tables["users"]
            roles = m.metadata.tables["user_roles"]
            for row in json.loads((ROOT / "app/seed/base/users.json").read_text()):
                session.execute(
                    insert(users)
                    .values(username=row["username"], display_name=row["display_name"])
                    .on_conflict_do_nothing()
                )
                user = session.execute(
                    select(users.c.id).where(users.c.username == row["username"])
                ).scalar_one()
                session.execute(
                    insert(roles).values(user_id=user, role=row["role"]).on_conflict_do_nothing()
                )
                if row["username"] in {"demo", "restricted"}:
                    if row["username"] == "restricted":
                        session.execute(
                            m.mission_grants.delete().where(
                                m.mission_grants.c.user_id == user,
                                m.mission_grants.c.mission_key != "msn-sim-01",
                            )
                        )
                    allowed = (
                        truth["missions"] if row["username"] == "demo" else truth["missions"][:1]
                    )
                    for mission in allowed:
                        session.execute(
                            insert(m.mission_grants)
                            .values(user_id=user, mission_key=mission["key"], silo=None)
                            .on_conflict_do_nothing()
                        )
            for mission in truth["missions"]:
                values = {
                    "key": mission["key"],
                    "title": mission["title"],
                    "current_stage": mission["currentStage"],
                    "simulated": True,
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                }
                session.execute(
                    insert(m.missions)
                    .values(**values)
                    .on_conflict_do_update(index_elements=[m.missions.c.key], set_=values)
                )
                for alias in mission["aliases"]:
                    session.execute(
                        insert(m.mission_aliases)
                        .values(alias=alias, mission_key=mission["key"])
                        .on_conflict_do_nothing()
                    )
                for name in ELEMENTS:
                    session.execute(
                        insert(m.elements)
                        .values(
                            id=uuid5(NAMESPACE_URL, mission["key"] + ":" + name),
                            mission_key=mission["key"],
                            silo="engineering",
                            name=name,
                        )
                        .on_conflict_do_nothing()
                    )
            for key, label, unit, minimum, maximum, whole, conversions, synonyms in PARAMETERS:
                session.execute(
                    insert(m.parameters)
                    .values(
                        key=key,
                        label=label,
                        unit=unit,
                        min_value=minimum,
                        max_value=maximum,
                        whole=whole,
                    )
                    .on_conflict_do_nothing()
                )
                for source, multiplier in conversions.items():
                    session.execute(
                        insert(m.unit_conversions)
                        .values(parameter=key, unit=source, multiplier=multiplier, value_offset=0)
                        .on_conflict_do_nothing()
                    )
                for language, header in zip(["en", "fr"], synonyms, strict=True):
                    session.execute(
                        insert(m.header_synonyms)
                        .values(header=header, parameter=key, language=language)
                        .on_conflict_do_nothing()
                    )
            for value in truth["values"]:
                citation = value["citation"]
                chunk = session.execute(
                    select(m.chunks.c.text)
                    .join(m.doc_versions)
                    .where(
                        m.doc_versions.c.item == citation["item"],
                        m.doc_versions.c.version == citation["version"],
                        m.chunks.c.block == citation["block"],
                    )
                ).scalar_one()
                if (
                    digest(chunk.splitlines()[value["rowIndex"]].encode())
                    != citation["quoteSha256"]
                ):
                    raise ValueError("seed citation mismatch")
                session.execute(
                    insert(m.cube_values)
                    .values(
                        id=UUID(value["id"]),
                        mission_key=value["mission"],
                        silo=value["silo"],
                        stage=value["stage"],
                        element_id=None,
                        parameter=value["parameter"],
                        kind=value["storedKind"],
                        low=value["storedLow"],
                        high=value["storedHigh"],
                        source_number=value["sourceNumber"],
                        source_unit=value["sourceUnit"],
                        valid_from=value["validFrom"],
                        recorded_at=datetime.fromisoformat(value["recordedAt"]),
                        item=citation["item"],
                        version=citation["version"],
                        block=citation["block"],
                        locator=citation["locator"],
                        quote_sha256=bytes.fromhex(citation["quoteSha256"]),
                        approval_request_id=None,
                        simulated=True,
                    )
                    .on_conflict_do_nothing()
                )
    finally:
        engine.dispose()


def seed(settings: Settings, number: int = 1) -> dict[str, int]:
    root = Path(settings.provider_root)
    truth, pending = prepare(root, number)
    index_provider(settings)
    load_cube(settings, truth)
    set_demo_passwords(settings)
    finish(root, truth, pending)
    index_provider(settings)
    return {
        "seed": number,
        "missions": len(truth["missions"]),
        "documents": truth["documents"],
        "questions": len(truth["questions"]),
        "values": len(truth["values"]),
    }
