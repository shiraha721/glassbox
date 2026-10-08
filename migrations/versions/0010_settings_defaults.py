"""Drop the old fixed temperature default and give saved settings the common download hosts.

Versions before 2026-10-07 saved temperature 0.2 for every provider, which models that only
accept their own temperature (often 1) reject; empty means the provider's default. Settings
saved with no download hosts get the hosts new installations start with. Data only: earlier
versions read the changed values as they are, so the downgrade changes nothing."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

OLD_TEMPERATURE = 0.2
# The list as of this revision; later changes to the defaults do not rewrite saved settings.
COMMON_DOWNLOAD_HOSTS = [
    "github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs-us-1.huggingface.co",
    "cas-bridge.xethub.hf.co",
    "dl.google.com",
    "storage.googleapis.com",
    "static.crates.io",
    "proxy.golang.org",
    "repo1.maven.org",
]


def upgrade() -> None:
    """Rewrite each saved settings record once, advancing its revision when it changes."""
    bind = op.get_bind()
    for identifier, data in bind.execute(sa.text("SELECT id, data FROM settings")).fetchall():
        config = json.loads(data) if isinstance(data, str) else data
        changed = False
        for profile in [*config.get("providers", []), config.get("provider") or {}]:
            if profile.get("temperature") == OLD_TEMPERATURE:
                profile["temperature"] = None
                changed = True
        if not config.get("network_hosts"):
            config["network_hosts"] = list(COMMON_DOWNLOAD_HOSTS)
            changed = True
        if changed:
            config["revision"] = int(config.get("revision", 0)) + 1
            bind.execute(
                sa.text("UPDATE settings SET data = :data WHERE id = :id"),
                {"data": json.dumps(config), "id": identifier},
            )


def downgrade() -> None:
    """Keep the values: they are valid settings for earlier versions too."""
