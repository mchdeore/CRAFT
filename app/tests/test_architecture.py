import re

from app.infrastructure.paths import MIGRATIONS

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


def test_feature_calls_use_handlers() -> None:
    import ast

    def allowed(source: str, feature: str) -> bool:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if (
                    len(parts) >= 3
                    and parts[0] == "app"
                    and parts[1] not in [feature, "infrastructure"]
                ):
                    if parts[2] in ["service", "storage"]:
                        return False
        return True

    assert not allowed("from app.library.service import search", "cube")
    assert allowed("from app.library.handlers import search", "cube")
