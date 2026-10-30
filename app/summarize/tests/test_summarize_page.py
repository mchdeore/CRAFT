"""Static-shell and operation route checks; browser behavior is checked separately."""

from fastapi.testclient import TestClient

from app import create_app
from app.infrastructure.operations.registry import registry


def test_summarize_page_persists_without_dev_mount(settings):
    app = create_app(settings)
    with TestClient(app, follow_redirects=False) as http:
        result = http.get("/summarize")
        assert result.status_code == 200 and result.headers["content-type"].startswith("text/html")
        assert result.headers["x-content-type-options"] == "nosniff"
        assert "object-src 'none'" in result.headers["content-security-policy"]
        assert http.get("/summarize/handlers.py").status_code == 404
        assert http.get("/dev").status_code == 404
        assert http.post("/api/summarize", json={}).status_code == 401
    assert {"summarize.selected", "summarize.upload"} <= set(registry)
