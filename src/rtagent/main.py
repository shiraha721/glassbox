"""FastAPI composition root and local browser access boundary."""

import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rtagent.api import router
from rtagent.config import Settings
from rtagent.domain import AppError
from rtagent.harness import Harness
from rtagent.hooks import Hooks


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


def create_app(
    settings: Settings | None = None,
    *,
    hooks: Hooks | None = None,
    configure: Callable[[Harness], None] | None = None,
) -> FastAPI:
    """Compose a local single-worker application without doing I/O at import time."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Own database, runner, and checkpoint resources for the process lifetime."""
        harness = Harness(settings, hooks)
        if configure:
            configure(harness)
        async with harness:
            app.state.settings = settings
            app.state.studio = harness.studio
            yield

    app = FastAPI(title="Security Agent Studio", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.exception_handler(AppError)
    async def app_error(_request: Request, error: AppError):
        return error_response(error)

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        """Reject foreign browser origins while allowing direct local clients."""
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            # Native clients omit these headers; browsers supply them for cross-site requests.
            if (origin is not None and origin not in settings.allowed_origins) or (
                request.headers.get("sec-fetch-site") == "cross-site"
            ):
                return error_response(
                    AppError("origin_denied", "Request origin is not allowed.", 403)
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
