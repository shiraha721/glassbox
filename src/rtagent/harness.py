"""A concrete resource owner and typed embedding entry point for the existing agent loop."""

import asyncio
from typing import Any

from rtagent.application import Studio
from rtagent.config import Settings
from rtagent.domain import AppError, PreviewRequest, ResumeRequest, RunView, uid
from rtagent.hooks import Hooks
from rtagent.runtime import Runner
from rtagent.storage import TERMINAL, Repository
from rtagent.tools import ToolRegistry


class Harness:
    """Share Studio, receipts, policy and checkpoints between Web and Python embedding."""

    def __init__(self, settings: Settings | None = None, hooks: Hooks | None = None) -> None:
        """Prepare resources without I/O; register explicit trusted tools before entering the context."""
        self.settings = settings or Settings()
        self.repository = Repository(self.settings.database_url)
        self.registry = ToolRegistry(self.settings.workspace_root, self.settings.data_dir)
        self.runner = Runner(
            self.repository,
            self.registry,
            str(self.settings.data_dir.resolve() / "checkpoints.db"),
            hooks,
        )
        self.studio = Studio(self.repository, self.registry, self.runner)
        self.entered = False

    async def __aenter__(self) -> "Harness":
        """Migrate, apply explicit startup options and open the single worker/checkpointer."""
        if self.entered:
            raise AppError("harness_open", "Use one active context per Harness instance.")
        self.settings.initialize()
        try:
            await self.repository.initialize()
            config = await self.repository.config()
            configured = self.settings.apply_provider(config)
            if configured != config:
                await self.repository.save_config(configured)
            await self.runner.open()
        except BaseException:
            await self.runner.close()
            await self.repository.close()
            raise
        self.entered = True
        return self

    async def __aexit__(self, *_exception: Any) -> None:
        """Stop summaries, Runs and processes before closing checkpoints and application storage."""
        try:
            await self.studio.close()
            await self.runner.close()
        finally:
            await self.repository.close()
            self.entered = False

    async def run(
        self,
        task: str,
        *,
        session_id: str | None = None,
        preset_id: str = "general-agent",
        variables: dict[str, Any] | None = None,
        skills: list[str] | None = None,
        key: str | None = None,
    ) -> RunView:
        """Submit observed revisions without requiring Preview and return the durable Run immediately."""
        if not self.entered:
            raise AppError("harness_closed", "Enter the Harness context before submitting work.")
        session = (
            await self.repository.session(session_id)
            if session_id
            else await self.repository.new_session()
        )
        preset, config = await self.repository.preset(preset_id), await self.repository.config()
        return await self.studio.start(
            PreviewRequest(
                session_id=session["id"],
                expected_session_revision=session["revision"],
                preset_id=preset.id,
                preset_revision=preset.revision,
                config_revision=config.revision,
                input=task,
                variables=variables or {},
                skills=skills or [],
            ),
            key or uid(),
        )

    async def wait(self, run_id: str, timeout: float = 600) -> RunView:
        """Wait for completion or attention; a client timeout never cancels execution."""
        try:
            async with asyncio.timeout(timeout):
                while True:
                    view = await self.studio.view(run_id)
                    if view.status in TERMINAL | {"waiting_approval", "interrupted"}:
                        return view
                    await asyncio.sleep(0.1)
        except TimeoutError:
            raise AppError(
                "wait_timeout", "Run is still active; inspect or cancel it explicitly.", 408
            ) from None

    async def approve(self, run_id: str, approval_id: str, decision: str) -> RunView:
        """Resolve the same persisted approval used by the Web UI."""
        if decision not in {"approve", "reject"}:
            raise AppError("approval_decision", "Choose approve or reject.")
        return await self.studio.approve(run_id, approval_id, decision)

    async def cancel(self, run_id: str) -> RunView:
        """Await native cleanup through the shared cancellation use case."""
        return await self.studio.cancel(run_id)

    async def resume(self, run_id: str, resolutions: ResumeRequest | None = None) -> RunView:
        """Resume the original snapshot with explicit unknown-outcome decisions."""
        return await self.studio.resume(run_id, resolutions or ResumeRequest())
