"""FastAPI composition root and local browser access boundary."""

import base64
import hashlib
import html
import logging
import secrets
import traceback
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from glassbox.domain import AppError
from glassbox.domain.hooks import Hooks
from glassbox.harness import Harness
from glassbox.infrastructure.config import Settings
from glassbox.infrastructure.desktop.folders import FolderPicker
from glassbox.infrastructure.providers.chatgpt import CALLBACK_PATH, USAGE_URL
from glassbox.interfaces.http.api import router

logger = logging.getLogger(__name__)


def error_response(error: AppError, request_id: str | None = None) -> JSONResponse:
    """Map expected failures to the stable API error envelope."""
    return JSONResponse(
        {
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
                "request_id": request_id or secrets.token_hex(8),
            }
        },
        status_code=error.status,
    )


# The sign-in page's only script: drop the one-time code from the address bar, and let the
# button close the tab ChatGPT returned to, or open Glassbox in it where closing is refused.
SIGN_IN_SCRIPT = (
    'history.replaceState(null, "", location.pathname);'
    'document.getElementById("done").addEventListener("click", () => {'
    'window.close(); setTimeout(() => location.assign("/"), 300); });'
)
SIGN_IN_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'; script-src 'sha256-"
    + base64.b64encode(hashlib.sha256(SIGN_IN_SCRIPT.encode()).digest()).decode()
    + "'"
)


def sign_in_page(title: str, message: str, status: int = 200, usage: bool = False) -> HTMLResponse:
    """The page that finishes Sign in with ChatGPT in its browser tab."""
    link = (
        f'<p><a href="{USAGE_URL}" target="_blank" rel="noreferrer">Manage usage</a> '
        "in ChatGPT shows what Glassbox has used and lets you limit it.</p>"
        if usage
        else ""
    )
    body = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)} · Glassbox</title><style>"
        # The colors are the design tokens of web/src/style.css; change them together.
        "body{font:15px/1.6 system-ui,sans-serif;max-width:30rem;margin:16vh auto;padding:0 24px;"
        "background:#f6f7f9;color:#1b2130}h1{font-size:22px}button{font:inherit;padding:7px 16px;"
        "border:0;border-radius:8px;background:#4f46e5;color:#fff;cursor:pointer}a{color:#4338ca}"
        "@media (prefers-color-scheme:dark){body{background:#0f1115;color:#e7e9ee}"
        "button{background:#7b73ff}a{color:#aaa5ff}}</style>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{link}"
        f'<button id="done" type="button">Got it</button><script>{SIGN_IN_SCRIPT}</script></html>'
    )
    return HTMLResponse(
        body,
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": SIGN_IN_POLICY,
            "Referrer-Policy": "no-referrer",
        },
    )


class LocalAccessMiddleware:
    """Apply the local browser boundary directly to ASGI without wrapping request receive streams."""

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        """Bind explicit browser origins; direct local clients require no login or token."""
        self.app, self.allowed_origins = app, allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Preserve streaming/disconnect signals and report failures before a response starts."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_headers(message: Message) -> None:
            """Add response headers at ASGI start without buffering SSE or replacing receive."""
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        if scope["path"].startswith("/api/"):
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            # The page served by this process is always allowed, whatever port it runs on.
            # TrustedHostMiddleware still rejects non-loopback Host values (DNS rebinding).
            same_origin = origin == f"{scope['scheme']}://{headers.get('host')}"
            if (
                origin is not None and not same_origin and origin not in self.allowed_origins
            ) or headers.get("sec-fetch-site") == "cross-site":
                await error_response(
                    AppError("origin_denied", "Request origin is not allowed.", 403)
                )(scope, receive, send_headers)
                return
        try:
            await self.app(scope, receive, send_headers)
        except Exception as error:
            identifier = secrets.token_hex(8)
            # Log source locations, not submitted inputs or potentially credential-bearing errors.
            frames = traceback.extract_tb(error.__traceback__)
            logger.error(
                "Service error request_id=%s type=%s stack=%s",
                identifier,
                type(error).__name__,
                " -> ".join(
                    f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames
                ),
            )
            if started:
                raise  # HTTP cannot replace an already streamed response with a second JSON body.
            await error_response(
                AppError(
                    "internal_error",
                    "Unexpected service error. Check the server log with the request ID.",
                    500,
                ),
                identifier,
            )(scope, receive, send_headers)


def create_app(
    settings: Settings | None = None,
    *,
    hooks: Hooks | None = None,
    configure: Callable[[Harness], None] | None = None,
) -> FastAPI:
    """Compose a local single-worker application without doing I/O at import time."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Own database, runner, and checkpoint resources for the process lifetime."""
        harness = Harness(settings, hooks)
        if configure:
            configure(harness)
        picker = FolderPicker()
        try:
            async with harness:
                app.state.settings = settings
                app.state.glassbox = harness.glassbox
                app.state.folder_picker = picker
                yield
        finally:
            await picker.close()

    app = FastAPI(title="Glassbox", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.exception_handler(AppError)
    async def app_error(_request: Request, error: AppError) -> JSONResponse:
        """Render application failures consistently for the Web, CLI and RPC clients."""
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError) -> JSONResponse:
        """Report actionable field errors without returning the submitted input values."""
        details = [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()]
        message = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in details)
        return error_response(AppError("request_validation", message, 422, details))

    app.add_middleware(LocalAccessMiddleware, allowed_origins=settings.allowed_origins)

    app.include_router(router)

    @app.get(CALLBACK_PATH, include_in_schema=False)
    async def chatgpt_callback(request: Request) -> HTMLResponse:
        """Finish Sign in with ChatGPT in the browser tab that ChatGPT returned to."""
        query = {name: request.query_params.getlist(name) for name in request.query_params}
        try:
            done = await request.app.state.glassbox.chatgpt.complete(query)
        except AppError as error:
            return sign_in_page("Sign-in did not complete", error.message, error.status)
        account = f" as {done.email}" if done.email else ""
        if not done.plan_usage:
            return sign_in_page(
                "ChatGPT plan not allowed",
                f"You are signed in{account}, but using your ChatGPT plan was not allowed, so "
                "this provider cannot send requests. In Glassbox, choose Continue with ChatGPT "
                "again and allow it.",
            )
        if done.registered:
            return sign_in_page(
                "You're using your ChatGPT plan",
                f"Signed in{account}. Requests from this provider now use your ChatGPT plan and "
                "count toward its usage limits. Glassbox gets no access to your ChatGPT "
                "conversations. You can close this tab.",
                usage=True,
            )
        return sign_in_page(
            "Signed in to ChatGPT",
            f"Signed in{account}. You can close this tab and return to Glassbox.",
            usage=True,
        )

    static_root = Path(__file__).resolve().parents[4] / "web" / "dist"

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> Response:
        """Serve the built SPA while preserving API misses and path containment."""
        if path == "api" or path.startswith("api/"):
            return error_response(AppError("not_found", "API route not found.", 404))
        target = (static_root / path).resolve()
        if target.is_relative_to(static_root) and target.is_file():
            return FileResponse(
                target,
                headers={
                    "Cache-Control": (
                        "public, max-age=31536000, immutable"
                        if path.startswith("assets/")
                        else "no-cache"
                    )
                },
            )
        if path.startswith("assets/"):
            return error_response(
                AppError("not_found", "Frontend asset not found. Reload the page.", 404)
            )
        index = static_root / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse(
            {
                "message": "Build the frontend with npm run build in web/, or use its development server."
            },
            status_code=503,
        )

    return app


def serve(port: int = 8001) -> None:
    """Start Glassbox on loopback; the browser boundary accepts its own origin."""
    import uvicorn

    uvicorn.run(
        "glassbox.interfaces.http.app:create_app", factory=True, host="127.0.0.1", port=port
    )


if __name__ == "__main__":
    serve()
