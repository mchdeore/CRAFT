"""The test settings ignore a developer's .env beyond the database, and no test reaches a model."""

import asyncio

import msgspec
import pytest

from app.agent import model
from app.tests.support import model as scripted_model


def test_settings_take_only_the_database_from_the_developer_env(settings):
    assert (settings.hub_url, settings.oidc_authority) == ("", "")
    assert scripted_model.scripted(settings)


def test_a_real_model_endpoint_is_refused(settings):
    real = msgspec.structs.replace(settings, model_endpoint="https://real.example")
    with pytest.raises(RuntimeError):
        model.client(real)
    with pytest.raises(RuntimeError):
        asyncio.run(model.embeddings(real, ["Synthetic text"]))
