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
