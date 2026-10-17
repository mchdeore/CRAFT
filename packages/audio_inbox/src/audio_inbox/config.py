"""Private controller configuration with one deferred credential reference."""

import json
from pathlib import Path

from .providers import AzureGoalSuggester, AzureTranscriber, ProviderAdapterError


def providers(path: Path):
    if path.is_symlink():
        raise ValueError("unsafe_configuration")
    config = json.loads(path.read_text())
    reference = config["key_ref"]
    filename, field = reference.split(":", 1)
    if Path(filename).name != filename or not filename or not field:
        raise ValueError("invalid_key_reference")
    key_path = path.parent / filename

    def key():
        if key_path.is_symlink():
            raise ProviderAdapterError("unsafe_key_reference")
        values = dict(
            line.split("=", 1)
            for line in key_path.read_text().splitlines()
            if "=" in line and not line.startswith("#")
        )
        return values.get(field, "")

    transcription = config.get("transcription")
    goals = config.get("goals")
    transcriber = AzureTranscriber(key_provider=key, **transcription) if transcription else None
    suggester = AzureGoalSuggester(key_provider=key, **goals) if goals else None
    return transcriber, suggester
