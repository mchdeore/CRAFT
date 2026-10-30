import ast
import re

import pytest

from app.infrastructure.paths import MIGRATIONS, ROOT

NAME = re.compile(r"(\d{4})_[a-z]+_[a-z0-9_]+\.py")


def test_migration_names_follow_rule() -> None:
    """NNNN_<verb>_<thing>.py with revision 'NNNN'. Passes empty in S0; guards S1."""
    for path in sorted((MIGRATIONS / "versions").glob("*.py")):
        match = NAME.fullmatch(path.name)
        assert match, f"{path.name}: expected NNNN_<verb>_<thing>.py"
        assert f'revision: str = "{match[1]}"' in path.read_text(), f"{path.name}: revision"


def test_every_route_is_an_operation(settings) -> None:
    import pytest

    from app import create_app
    from app.infrastructure.operations.registry import validate_routes
    from app.infrastructure.operations.tests.fixtures.undecorated_route import bad_app

    validate_routes(create_app(settings))
    with pytest.raises(ValueError):
        validate_routes(bad_app())


def test_observe_never_stacks_on_operation() -> None:
    import pytest

    from app.infrastructure.operations import observe
    from app.infrastructure.operations.registry import registry
    from app.infrastructure.operations.tests.fixtures.observed_operation import invalid_stack

    snapshot = registry.copy()
    try:
        for reverse in [True, False]:
            with pytest.raises(ValueError):
                invalid_stack(reverse)
            registry.clear()
            registry.update(snapshot)

        def private() -> None:
            pass

        observed = observe("private")(private)
        assert getattr(observed, "__observed__", False)
    finally:
        registry.clear()
        registry.update(snapshot)


def forbidden_imports(source: str, feature: str, package: str) -> list[tuple[int, str]]:
    violations = []
    for node in ast.walk(ast.parse(source)):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            prefix = (
                package.split(".")[: len(package.split(".")) - node.level + 1] if node.level else []
            )
            module = (
                ".".join([*prefix, *node.module.split(".")]) if node.module else ".".join(prefix)
            )
            modules = [module, *(module + "." + alias.name for alias in node.names)]
        else:
            continue
        for module in modules:
            parts = module.split(".")
            if (
                len(parts) >= 3
                and parts[0] == "app"
                and parts[1] not in {feature, "infrastructure"}
                and parts[2] in {"service", "storage"}
            ):
                violation = (node.lineno, ".".join(parts[:3]))
                if violation not in violations:
                    violations.append(violation)
    return violations


@pytest.mark.parametrize(
    "source,allowed",
    [
        ("from app.library.service import search", False),
        ("import app.library.storage as storage", False),
        ("from app.library import service", False),
        ("from ..library import storage", False),
        ("from app.library.handlers import search", True),
        ("from .storage import owned", True),
        ("from app.infrastructure.operations import operation", True),
    ],
)
def test_feature_import_rule_covers_import_forms(source, allowed):
    assert (not forbidden_imports(source, "cube", "app.cube")) is allowed


def test_feature_calls_use_handlers() -> None:
    violations = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        relative = path.relative_to(ROOT / "app")
        if "tests" in relative.parts or len(relative.parts) < 2:
            continue
        feature = relative.parts[0]
        if feature == "infrastructure":
            continue
        package = ".".join(["app", *relative.parts[:-1]])
        violations.extend(
            f"{relative}:{line}: {module}"
            for line, module in forbidden_imports(path.read_text(), feature, package)
        )
    assert not violations, "\n".join(violations)


def test_infrastructure_resolves_accounts_through_its_interface() -> None:
    """`access.py` and `mcp/stdio.py` name no accounts table and don't use metadata."""
    from app.accounts import storage

    accounts_tables = {
        table.name
        for table in [
            storage.users,
            storage.user_roles,
            storage.auth_sessions,
            storage.workspace_tokens,
            storage.local_credentials,
        ]
    }
    for path in ["app/infrastructure/access.py", "app/infrastructure/mcp/stdio.py"]:
        source = (ROOT / path).read_text()
        assert "metadata" not in source, path
        assert not set(re.findall(r'"(\w+)"', source)) & accounts_tables, path
