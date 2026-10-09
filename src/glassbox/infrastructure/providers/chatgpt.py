"""Sign in with ChatGPT: OpenAI's OAuth flow that lets a local app use the user's ChatGPT plan.

This follows OpenAI's documented flow for open-source and locally hosted apps
(https://developers.openai.com/siwc/token-sharing-open-source): the first sign-in registers
the app dynamically and returns its client ID; every sign-in uses PKCE, state and nonce and
names this installation by a stable host ID; the ID token is verified against OpenAI's keys;
refresh tokens rotate. The browser returns to `CALLBACK_PATH` on this server, which finishes
the exchange. Tokens are issued for the OpenAI API alone (`CHATGPT_API`), stay in
`chatgpt.json` in the data directory (mode 0600 on POSIX) and are never sent to the browser
or to any other host."""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from glassbox.domain import CHATGPT_API, AppError, ChatGPTStatus
from glassbox.infrastructure.providers import USER_AGENT

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
REVOKE_URL = f"{ISSUER}/api/accounts/oauth/revoke"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
# Identity, a refresh token, and permission to send requests on the user's plan.
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"
# ChatGPT accepts any loopback port but only this path, on 127.0.0.1.
CALLBACK_PATH = "/auth/callback"
USAGE_URL = "https://chatgpt.com/settings/usage"
APP_NAME = "Glassbox"
REGISTRATION = "dynamic_agent_client"
PENDING_SECONDS = 600
# Access tokens last an hour; renew one that has less than this left before a request.
REFRESH_MARGIN = 300
# Refresh errors after which the saved tokens can never work again.
REVOKED = {
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
}


@dataclass
class Attempt:
    """One sign-in started in the browser, until ChatGPT returns to the callback."""

    provider_id: str
    verifier: str
    nonce: str
    redirect_uri: str
    client_id: str | None
    started: float


@dataclass
class Completion:
    """A finished sign-in, for the page the browser lands on."""

    provider_id: str
    email: str | None
    registered: bool
    plan_usage: bool


class SignInRefused(Exception):
    """OpenAI's sign-in service answered with an OAuth error."""

    def __init__(self, code: str, status: int) -> None:
        """Keep the OAuth error code; its description is not shown."""
        super().__init__(code)
        self.code, self.status = code, status


def unavailable() -> AppError:
    """A sign-in service failure that keeps the saved sign-in and may be retried."""
    return AppError(
        "chatgpt_unavailable",
        "ChatGPT's sign-in service could not be reached. Check the connection and try again.",
        502,
        {"transient": True},
    )


def plan_not_allowed() -> AppError:
    """The sign-in does not, or no longer, allow requests on the plan."""
    return AppError(
        "chatgpt_plan_not_allowed",
        "Using the ChatGPT plan was not allowed at sign-in. Continue with ChatGPT again in "
        "Settings → Providers and allow it.",
        403,
    )


class ChatGPTSignIn:
    """ChatGPT sign-ins of this installation, one per provider ID, with their shared host ID.

    A sign-in record keeps the issued client ID after sign-out, so signing in again reuses the
    registration as OpenAI asks. Refreshes are serialized so a rotating refresh token is never
    sent twice."""

    def __init__(self, path: Path | None) -> None:
        """Use the given file, or none: then nobody can sign in."""
        self.path = path
        self.attempts: dict[str, Attempt] = {}
        self.lock = asyncio.Lock()
        self.keys: jwt.PyJWKSet | None = None

    def _load(self) -> dict[str, Any]:
        if self.path is None or not self.path.exists():
            return {"host_id": None, "accounts": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        if self.path is None:
            raise AppError("chatgpt_unavailable", "This service has no data directory.", 409)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False, prefix=".chatgpt-"
        ) as handle:
            temporary = Path(handle.name)
            # Restrict the file before it receives any token (a no-op for ACLs on Windows).
            os.chmod(temporary, 0o600)
            json.dump(data, handle)
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    async def _read(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._load)

    async def _save(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._write, data)

    async def start(self, provider_id: str, redirect_uri: str) -> str:
        """Begin a sign-in for a provider and return the ChatGPT page to open in the browser."""
        async with self.lock:
            data = await self._read()
            if not data.get("host_id"):
                # Opaque and stable per installation; chosen before the first sign-in.
                data["host_id"] = f"urn:uuid:{uuid.uuid4()}"
                await self._save(data)
        account = data["accounts"].get(provider_id, {})
        client_id = account.get("client_id")
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        parameters = {
            "client_id": client_id or REGISTRATION,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "resource": CHATGPT_API,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge.rstrip(b"=").decode(),
            "ext_agent_host_id": data["host_id"],
        }
        if client_id is None:
            parameters["agent_name_hint"] = APP_NAME
        else:
            if account.get("email"):
                parameters["login_hint"] = account["email"]
            # A returning sign-in skips the consent screen unless asked, so plan usage that
            # was declined before could otherwise never be allowed.
            if account.get("plan_declined"):
                parameters["prompt"] = "consent"
        now = time.monotonic()
        self.attempts = {
            key: value
            for key, value in self.attempts.items()
            if now - value.started < PENDING_SECONDS
        }
        self.attempts[state] = Attempt(provider_id, verifier, nonce, redirect_uri, client_id, now)
        return f"{AUTHORIZE_URL}?{urlencode(parameters)}"

    async def complete(self, query: Mapping[str, list[str]]) -> Completion:
        """Finish a sign-in from ChatGPT's redirect: check it, exchange the code, keep tokens."""

        def single(name: str) -> str | None:
            values = query.get(name, [])
            if len(values) > 1:
                raise AppError("chatgpt_sign_in", f"The sign-in response repeats {name}.", 400)
            return values[0] if values else None

        attempt = self.attempts.pop(single("state") or "", None)
        if attempt is None or time.monotonic() - attempt.started >= PENDING_SECONDS:
            raise AppError(
                "chatgpt_sign_in",
                "This sign-in has expired or was already used. Start it again from Glassbox.",
                400,
            )
        if error := single("error"):
            raise AppError(
                "chatgpt_sign_in",
                (
                    "Sign-in was cancelled in ChatGPT."
                    if error == "access_denied"
                    else f"ChatGPT did not complete the sign-in ({error[:80]})."
                ),
                400,
            )
        code, returned = single("code"), single("client_id")
        client_id = returned or attempt.client_id
        if (
            not code
            or not client_id
            or client_id == REGISTRATION
            or (attempt.client_id and returned and returned != attempt.client_id)
        ):
            raise AppError(
                "chatgpt_sign_in", "ChatGPT did not complete the registration. Try again.", 400
            )
        async with self.lock:
            if attempt.client_id is None:
                # Keep the issued client ID first: the code exchange can still fail, and a
                # new attempt must reuse the registration instead of creating another.
                data = await self._read()
                data["accounts"][attempt.provider_id] = {"client_id": client_id}
                await self._save(data)
            try:
                tokens = await self._token(
                    {
                        "grant_type": "authorization_code",
                        "client_id": client_id,
                        "code": code,
                        "code_verifier": attempt.verifier,
                        "redirect_uri": attempt.redirect_uri,
                        "resource": CHATGPT_API,
                    }
                )
            except SignInRefused as refused:
                raise AppError(
                    "chatgpt_sign_in",
                    f"ChatGPT did not accept the sign-in ({refused.code}). Try again.",
                    400,
                ) from None
            claims = await self._identity(tokens.get("id_token"), client_id, attempt.nonce)
            data = await self._read()
            account = data["accounts"].get(attempt.provider_id, {})
            if account.get("subject") and account["subject"] != claims["sub"]:
                # The registration belongs to the account that created it.
                raise AppError(
                    "chatgpt_sign_in",
                    "This is a different ChatGPT account than this provider's. To use it, add "
                    "another ChatGPT plan provider, or remove this one and add it again.",
                    400,
                )
            identity = {
                "client_id": client_id,
                "subject": claims["sub"],
                "email": claims.get("email") if isinstance(claims.get("email"), str) else None,
                "id_token": tokens["id_token"],
            }
            plan = PLAN_SCOPE in str(tokens.get("scope") or "").split()
            # Without plan usage the sign-in is of no use here; keep only who signed in.
            account = identity | (
                self._credentials(tokens, []) if plan else {"plan_declined": True}
            )
            data["accounts"][attempt.provider_id] = account
            await self._save(data)
        return Completion(
            attempt.provider_id,
            account["email"],
            registered=attempt.client_id is None,
            plan_usage=plan,
        )

    async def access_token(self, provider_id: str) -> str:
        """A current access token for a provider's sign-in, renewed shortly before it expires."""
        async with self.lock:
            data = await self._read()
            account = data["accounts"].get(provider_id)
            if account and account.get("plan_declined"):
                raise plan_not_allowed()
            if not account or not account.get("refresh_token"):
                raise AppError(
                    "chatgpt_signed_out",
                    "This provider uses a ChatGPT plan. Sign in with Continue with ChatGPT in "
                    "Settings → Providers.",
                    401,
                )
            # Plan usage can be withdrawn in ChatGPT; a renewal then grants fewer scopes.
            if PLAN_SCOPE not in account.get("scopes", []):
                raise plan_not_allowed()
            if account["expires_at"] - time.time() > REFRESH_MARGIN:
                return account["access_token"]
            try:
                tokens = await self._token(
                    {
                        "grant_type": "refresh_token",
                        "client_id": account["client_id"],
                        "refresh_token": account["refresh_token"],
                        "resource": CHATGPT_API,
                    }
                )
            except SignInRefused as refused:
                if refused.code not in REVOKED:
                    raise unavailable() from None
                # The grant is gone for good (revoked, expired or reused): forget its tokens.
                data["accounts"][provider_id] = self._signed_out(account)
                await self._save(data)
                raise AppError(
                    "chatgpt_sign_in_expired",
                    "The ChatGPT sign-in has expired or was disconnected. Continue with ChatGPT "
                    "again in Settings → Providers.",
                    401,
                ) from None
            if tokens.get("id_token"):
                claims = await self._identity(tokens["id_token"], account["client_id"], None)
                if claims["sub"] != account["subject"]:
                    raise AppError(
                        "chatgpt_sign_in", "The renewed sign-in names a different account.", 401
                    )
                account["id_token"] = tokens["id_token"]
            account.update(self._credentials(tokens, account["scopes"]))
            data["accounts"][provider_id] = account
            await self._save(data)
            return account["access_token"]

    async def status(self, provider_id: str) -> ChatGPTStatus:
        """Whether a provider is signed in and may use the plan, without any token."""
        account = (await self._read())["accounts"].get(provider_id) or {}
        signed_in = bool(account.get("refresh_token"))
        return ChatGPTStatus(
            signed_in=signed_in,
            email=account.get("email"),
            plan_usage=signed_in and PLAN_SCOPE in account.get("scopes", []),
        )

    async def forget(self, provider_id: str) -> None:
        """Sign out and drop the registration too, for a provider removed from Settings."""
        await self.sign_out(provider_id)
        async with self.lock:
            data = await self._read()
            if data["accounts"].pop(provider_id, None) is not None:
                await self._save(data)

    async def sign_out(self, provider_id: str) -> bool:
        """Forget a provider's tokens and revoke them at OpenAI; True when OpenAI confirmed.

        The local tokens are removed even when revocation fails; the app can then still be
        disconnected in ChatGPT's settings."""
        async with self.lock:
            data = await self._read()
            account = data["accounts"].get(provider_id)
            if not account or not account.get("refresh_token"):
                return True
            data["accounts"][provider_id] = self._signed_out(account)
            await self._save(data)
        form = {
            "token": account["refresh_token"],
            "token_type_hint": "refresh_token",
            "client_id": account["client_id"],
        }
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=15, follow_redirects=False, trust_env=False
                ) as client:
                    response = await client.post(
                        REVOKE_URL, data=form, headers={"User-Agent": USER_AGENT}
                    )
                if response.status_code == 200:
                    return True
                if response.status_code < 500:
                    return False
            except httpx.TransportError:
                pass
            if attempt == 0:
                await asyncio.sleep(0.3)
        return False

    @staticmethod
    def _signed_out(account: dict[str, Any]) -> dict[str, Any]:
        """Keep the registration and identity, drop every token."""
        return {key: account[key] for key in ("client_id", "subject", "email") if key in account}

    @staticmethod
    def _credentials(tokens: dict[str, Any], scopes: list[str]) -> dict[str, Any]:
        """The token fields to keep from a token response; a refresh may omit the scopes."""
        if (
            not isinstance(tokens.get("access_token"), str)
            or not isinstance(tokens.get("refresh_token"), str)
            or str(tokens.get("token_type", "")).lower() != "bearer"
            or not isinstance(tokens.get("expires_in"), int | float)
            or tokens["expires_in"] <= 0
        ):
            raise AppError(
                "chatgpt_sign_in", "ChatGPT returned incomplete sign-in credentials.", 502
            )
        granted = tokens.get("scope")
        return {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": time.time() + tokens["expires_in"],
            "scopes": granted.split() if isinstance(granted, str) else scopes,
            "saved_at": datetime.now(UTC).isoformat(),
        }

    async def _token(self, form: dict[str, str]) -> dict[str, Any]:
        """Call the token endpoint; OAuth errors raise SignInRefused with their code."""
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.post(
                    TOKEN_URL,
                    data=form,
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                )
        except httpx.TransportError:
            raise unavailable() from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code != 200:
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                error = error.get("code")
            if response.status_code >= 500 or not isinstance(error, str):
                raise unavailable()
            raise SignInRefused(error[:80], response.status_code)
        if not isinstance(body, dict):
            raise AppError("chatgpt_sign_in", "ChatGPT returned an unreadable sign-in.", 502)
        return body

    async def _identity(self, token: Any, client_id: str, nonce: str | None) -> dict[str, Any]:
        """Verify an ID token's signature, issuer, audience, expiry and nonce; return claims."""
        if not isinstance(token, str):
            raise AppError("chatgpt_sign_in", "ChatGPT returned no verifiable identity.", 502)
        for fresh in (False, True):
            if self.keys is None or fresh:
                self.keys = await self._fetch_keys()
            try:
                kid = jwt.get_unverified_header(token).get("kid")
                key = next((item for item in self.keys.keys if item.key_id == kid), None)
                if key is None:
                    continue  # Keys may have rotated; fetch them once more.
                claims = jwt.decode(
                    token,
                    key=key.key,
                    algorithms=["RS256"],
                    audience=client_id,
                    issuer=ISSUER,
                    leeway=5,
                    options={"require": ["iss", "aud", "exp", "iat", "sub"]},
                )
            except jwt.PyJWTError:
                break
            if (nonce is None or claims.get("nonce") == nonce) and claims.get(
                "azp", client_id
            ) == client_id:
                return claims
            break
        raise AppError(
            "chatgpt_sign_in", "ChatGPT's sign-in could not be verified. Try again.", 502
        )

    async def _fetch_keys(self) -> jwt.PyJWKSet:
        """OpenAI's published signing keys."""
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.get(JWKS_URL, headers={"User-Agent": USER_AGENT})
            if response.status_code != 200:
                raise unavailable()
            return jwt.PyJWKSet.from_dict(response.json())
        except (httpx.TransportError, ValueError, jwt.PyJWTError):
            raise unavailable() from None
