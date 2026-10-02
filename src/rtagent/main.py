"""FastAPI composition root and local browser access boundary."""

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rtagent.api import router
from rtagent.application import Studio
from rtagent.config import Settings
from rtagent.domain import AppError
from rtagent.runtime import Runner
from rtagent.storage import Repository
from rtagent.tools import ToolRegistry


def error_response(error: AppError) -> JSONResponse:
    """Map expected failures to the stable API error envelope."""
    return JSONResponse(
        {
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
                "request_id": secrets.token_hex(8),
            }
        },
        status_code=error.status,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Compose a local single-worker application without doing I/O at import time."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Own database, runner, and checkpoint resources for the process lifetime."""
        settings.initialize()
        repository = Repository(settings.database_url)
        await repository.initialize()
        registry = ToolRegistry(settings.workspace_root)
        runner = Runner(repository, registry, str(settings.data_dir / "checkpoints.db"))
        app.state.settings = settings
        app.state.studio = Studio(repository, registry, runner)
        await runner.open()
        try:
            yield
        finally:
            await runner.close()
            await repository.close()

    app = FastAPI(title="Security Agent Studio", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.exception_handler(AppError)
    async def app_error(_request: Request, error: AppError):
        return error_response(error)

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        """Require local origins, authenticated cookies, and CSRF for mutations."""
        path = request.url.path
        if path.startswith("/api/") and path != "/api/v1/health":
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.headers.get("origin") not in settings.allowed_origins:
                    return error_response(
                        AppError("origin_denied", "Request origin is not allowed.", 403)
                    )
            if not (path == "/api/v1/auth/session" and request.method == "POST"):
                try:
                    session = await app.state.studio.repository.browser_session(
                        request.cookies.get("studio_session", "")
                    )
                except AppError:
                    return error_response(
                        AppError("unauthenticated", "Unlock the local workspace first.", 401)
                    )
                request.state.browser_session = session
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
                    session["csrf"], request.headers.get("x-csrf-token", "")
                ):
                    return error_response(
                        AppError("csrf_denied", "Refresh the local session before submitting.", 403)
                    )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.include_router(router)
    static_root = Path(__file__).resolve().parents[2] / "web" / "dist"

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        """Serve the built SPA while preserving API misses and path containment."""
        if path.startswith("api/"):
            return JSONResponse(
                {"error": {"code": "not_found", "message": "API route not found."}}, status_code=404
            )
        target = (static_root / path).resolve()
        if target.is_relative_to(static_root) and target.is_file():
            return FileResponse(target)
        index = static_root / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse(
            {
                "message": "Build the frontend with npm run build in web/, or use its development server."
            },
            status_code=503,
        )

    return app


def serve() -> None:
    """Start the local studio on its dedicated default port."""
    import uvicorn

    uvicorn.run("rtagent.main:create_app", factory=True, host="127.0.0.1", port=8001)


if __name__ == "__main__":
    serve()
