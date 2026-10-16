import re

from app.infrastructure.paths import MIGRATIONS

NAME = re.compile(r"(\d{4})_[a-z]+_[a-z0-9_]+\.py")


def test_migration_names_follow_rule() -> None:
    """NNNN_<verb>_<thing>.py with revision 'NNNN'. Passes empty in S0; guards S1."""
    for path in sorted((MIGRATIONS / "versions").glob("*.py")):
        match = NAME.fullmatch(path.name)
        assert match, f"{path.name}: expected NNNN_<verb>_<thing>.py"
        assert f'revision: str = "{match[1]}"' in path.read_text(), f"{path.name}: revision"
