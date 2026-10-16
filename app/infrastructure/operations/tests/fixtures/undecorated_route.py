"""An isolated intentionally invalid application fixture."""

from fastapi import FastAPI


def bad_app() -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.add_api_route("/bad", lambda: None, methods=["GET"])
    return app
