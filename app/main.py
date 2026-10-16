"""What uvicorn runs: uv run uvicorn app.main:app"""

from app import create_app
from app.infrastructure.config.settings import Settings

app = create_app(Settings.from_env())
