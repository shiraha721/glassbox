"""Isolated application resources shared by offline integration tests."""

import httpx
import pytest

from glassbox.infrastructure.config import Settings
from glassbox.interfaces.http.app import create_app


@pytest.fixture
async def glassbox_client(tmp_path):
    """Start the local app with temporary storage and direct HTTP access."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("Local tool evidence", encoding="utf-8")
    settings = Settings.without_env_file(
        data_dir=tmp_path / "data", workspace_root=workspace, provider="fake"
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            yield client, app
