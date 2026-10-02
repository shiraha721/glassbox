"""Isolated application resources shared by offline integration tests."""

import httpx
import pytest

from rtagent.config import Settings
from rtagent.main import create_app


@pytest.fixture
async def studio_client(tmp_path):
    """Start the real app lifespan with temporary storage and authenticated HTTP access."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("Local tool evidence", encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data", workspace_root=workspace, access_token="test-local-token"
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            headers={"Origin": "http://127.0.0.1:8000"},
        ) as client:
            response = await client.post("/api/v1/auth/session", json={"token": "test-local-token"})
            assert response.status_code == 200, response.text
            client.headers["x-csrf-token"] = response.json()["csrf_token"]
            yield client, app
