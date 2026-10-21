"""Library text embeddings through the configured embedding model."""


def model_name(settings) -> str:
    return settings.model_embedding


def embed(text: str, caller) -> list[float]:
    from app.agent.handlers import EmbeddingInput
    from app.agent.handlers import embed as configured

    return configured(caller, EmbeddingInput(text=text)).vector


def embed_many(texts: list[str], caller) -> list[list[float]]:
    if not texts:
        return []
    from app.agent.handlers import EmbeddingBatchInput, embed_batch

    return embed_batch(caller, EmbeddingBatchInput(texts=texts)).vectors
