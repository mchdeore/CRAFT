"""Library text embeddings through the configured embedding model."""


def model_name(settings) -> str:
    return settings.model_embedding


def embed(text: str, caller) -> list[float]:
    from app.agent.handlers import EmbeddingInput
    from app.agent.handlers import embed as configured

    return configured(caller, EmbeddingInput(text=text)).vector
