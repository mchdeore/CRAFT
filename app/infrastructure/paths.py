"""Project paths, independent of the working directory."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "app/infrastructure/database/migrations"
