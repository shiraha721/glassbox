import pytest

from rtagent.domain import AppError
from rtagent.infrastructure.storage import Repository


async def test_migration_persistence_and_revision_conflict(tmp_path):
    repository = Repository("sqlite+aiosqlite:///" + (tmp_path / "app.db").as_posix())
    await repository.initialize()
    config = await repository.config()
    assert config.revision == 1
    saved = await repository.save_config(config)
    assert saved.revision == 2
    with pytest.raises(AppError) as error:
        await repository.save_config(config)
    assert error.value.status == 409
    preset = (await repository.list_presets())[0]
    assert preset.blocks
    session = await repository.new_session("Stored")
    await repository.close()
    reopened = Repository(repository.url)
    await reopened.initialize()
    assert (await reopened.session(session["id"]))["title"] == "Stored"
    assert (await reopened.config()).revision == 2
    await reopened.close()


async def test_saved_settings_drop_the_old_temperature_and_gain_download_hosts(tmp_path):
    """Migration 0010 rewrites settings an older version saved, and only what it must."""
    import json
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from rtagent.domain import COMMON_DOWNLOAD_HOSTS

    path = tmp_path / "app.db"
    alembic = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(alembic, "0009")
    old = {
        "revision": 4,
        "provider": {"profile_id": "openai", "model": "m", "temperature": 0.2},
        "providers": [
            {"id": "openai", "name": "A", "base_url": "https://a/v1", "temperature": 0.2},
            {"id": "warm", "name": "B", "base_url": "https://b/v1", "temperature": 1.0},
        ],
        "network_hosts": [],
    }
    with sqlite3.connect(path) as database:
        database.execute("INSERT INTO settings VALUES ('default', ?)", (json.dumps(old),))
    command.upgrade(alembic, "head")
    with sqlite3.connect(path) as database:
        new = json.loads(database.execute("SELECT data FROM settings").fetchone()[0])
    assert new["revision"] == 5
    assert new["provider"]["temperature"] is None
    assert [item["temperature"] for item in new["providers"]] == [None, 1.0]
    assert new["network_hosts"] == list(COMMON_DOWNLOAD_HOSTS)
    # Running it again changes nothing, and the downgrade keeps the values.
    command.downgrade(alembic, "0009")
    command.upgrade(alembic, "head")
    with sqlite3.connect(path) as database:
        assert json.loads(database.execute("SELECT data FROM settings").fetchone()[0]) == new
