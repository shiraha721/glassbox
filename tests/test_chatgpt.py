"""Sign in with ChatGPT: the OAuth flow, renewal, sign-out and plan requests, on fake servers."""

import base64
import hashlib
import json
import os
import stat
import time
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from test_api import start, wait_state
from test_providers import mock_client
from test_responses import completed, events

from glassbox.domain import (
    CHATGPT_API,
    AppError,
    Block,
    GlassboxConfig,
    Preset,
    ProviderConfig,
    ProviderProfile,
)
from glassbox.domain.prompts import compile_prompt
from glassbox.infrastructure.providers.catalog import list_models
from glassbox.infrastructure.providers.chat import generate, prepare, transformations
from glassbox.infrastructure.providers.chatgpt import (
    ISSUER,
    SCOPES,
    USAGE_URL,
    ChatGPTSignIn,
)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(RSAAlgorithm.to_jwk(KEY.public_key())) | {"kid": "k1"}
CALLBACK = "http://127.0.0.1:8001/auth/callback"
PLAN = ProviderConfig(
    profile_id="plan",
    adapter="responses",
    auth="chatgpt",
    base_url=CHATGPT_API,
    model="gpt-x",
    credential_ref="",
)


class OpenAI:
    """Fake auth.openai.com and api.openai.com that record every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.nonce: str | None = None
        self.issued = 0
        self.refresh_error: str | None = None
        self.subject = "user-1"
        self.scopes = SCOPES
        self.reply: Callable[[httpx.Request], httpx.Response] = lambda _request: httpx.Response(404)

    def id_token(self, nonce: str | None) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": "oaiapp_test",
            "sub": self.subject,
            "email": "ada@example.com",
            "iat": now,
            "exp": now + 3600,
        }
        if nonce:
            claims["nonce"] = nonce
        return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.openai.com":
            return self.reply(request)
        if request.url.path.endswith("/jwks.json"):
            return httpx.Response(200, json={"keys": [JWK]})
        if request.url.path.endswith("/revoke"):
            return httpx.Response(200)
        form = parse_qs(request.content.decode())
        grant = form["grant_type"][0]
        if grant == "refresh_token" and self.refresh_error:
            return httpx.Response(400, json={"error": self.refresh_error})
        self.issued += 1
        tokens = {
            "access_token": f"access-{self.issued}",
            "refresh_token": f"refresh-{self.issued}",
            "id_token": self.id_token(self.nonce if grant == "authorization_code" else None),
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": self.scopes,
        }
        if "offline_access" not in self.scopes:
            del tokens["refresh_token"]
        return httpx.Response(200, json=tokens)

    def forms(self, path: str) -> list[dict[str, list[str]]]:
        return [
            parse_qs(request.content.decode())
            for request in self.requests
            if request.url.path.endswith(path)
        ]


async def signed_in(tmp_path, monkeypatch) -> tuple[ChatGPTSignIn, OpenAI, dict[str, list[str]]]:
    fake = OpenAI()
    mock_client(monkeypatch, "chatgpt", fake)
    sign_in = ChatGPTSignIn(tmp_path / "chatgpt.json")
    query = parse_qs(urlparse(await sign_in.start("plan", CALLBACK)).query)
    fake.nonce = query["nonce"][0]
    done = await sign_in.complete(
        {"state": query["state"], "code": ["code-1"], "client_id": ["oaiapp_test"]}
    )
    assert done.registered and done.plan_usage and done.email == "ada@example.com"
    return sign_in, fake, query


async def test_the_first_sign_in_registers_the_app_and_keeps_tokens_here(tmp_path, monkeypatch):
    sign_in, fake, query = await signed_in(tmp_path, monkeypatch)
    single = {key: value[0] for key, value in query.items()}
    assert single | {"state": "", "nonce": "", "code_challenge": "", "ext_agent_host_id": ""} == {
        "client_id": "dynamic_agent_client",
        "agent_name_hint": "Glassbox",
        "response_type": "code",
        "redirect_uri": CALLBACK,
        "scope": SCOPES,
        "resource": CHATGPT_API,
        "code_challenge_method": "S256",
        "state": "",
        "nonce": "",
        "code_challenge": "",
        "ext_agent_host_id": "",
    }
    assert single["ext_agent_host_id"].startswith("urn:uuid:")
    (exchange,) = fake.forms("/oauth/token")
    verifier = exchange["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge.rstrip(b"=").decode() == single["code_challenge"]
    assert {key: value[0] for key, value in exchange.items() if key != "code_verifier"} == {
        "grant_type": "authorization_code",
        "client_id": "oaiapp_test",
        "code": "code-1",
        "redirect_uri": CALLBACK,
        "resource": CHATGPT_API,
    }
    # Tokens stay in an owner-only file; the status shown to the browser has none.
    path = tmp_path / "chatgpt.json"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "refresh-1" in path.read_text()
    status = await sign_in.status("plan")
    assert status.model_dump() == {
        "signed_in": True,
        "email": "ada@example.com",
        "plan_usage": True,
    }
    # The state is single-use.
    with pytest.raises(AppError, match="expired or was already used"):
        await sign_in.complete({"state": query["state"], "code": ["code-1"]})
    # Signing in again reuses the registration and the host ID.
    again = parse_qs(urlparse(await sign_in.start("plan", CALLBACK)).query)
    assert again["client_id"] == ["oaiapp_test"] and "agent_name_hint" not in again
    assert again["login_hint"] == ["ada@example.com"]
    assert again["ext_agent_host_id"] == query["ext_agent_host_id"]


async def test_tokens_are_renewed_before_expiry_and_forgotten_when_revoked(tmp_path, monkeypatch):
    sign_in, fake, _query = await signed_in(tmp_path, monkeypatch)
    assert await sign_in.access_token("plan") == "access-1"
    path = tmp_path / "chatgpt.json"

    def expire() -> None:
        data = json.loads(path.read_text())
        data["accounts"]["plan"]["expires_at"] = time.time() + 10
        path.write_text(json.dumps(data))

    expire()
    assert await sign_in.access_token("plan") == "access-2"
    (renewal,) = fake.forms("/oauth/token")[1:]
    assert {key: value[0] for key, value in renewal.items()} == {
        "grant_type": "refresh_token",
        "client_id": "oaiapp_test",
        "refresh_token": "refresh-1",
        "resource": CHATGPT_API,
    }
    assert "refresh-2" in path.read_text()
    expire()
    fake.refresh_error = "refresh_token_reused"
    with pytest.raises(AppError) as failure:
        await sign_in.access_token("plan")
    assert failure.value.code == "chatgpt_sign_in_expired"
    assert not (await sign_in.status("plan")).signed_in
    assert json.loads(path.read_text())["accounts"]["plan"] == {
        "client_id": "oaiapp_test",
        "subject": "user-1",
        "email": "ada@example.com",
    }


async def test_sign_out_revokes_the_refresh_token(tmp_path, monkeypatch):
    sign_in, fake, _query = await signed_in(tmp_path, monkeypatch)
    assert await sign_in.sign_out("plan") is True
    (revocation,) = fake.forms("/revoke")
    assert {key: value[0] for key, value in revocation.items()} == {
        "token": "refresh-1",
        "token_type_hint": "refresh_token",
        "client_id": "oaiapp_test",
    }
    with pytest.raises(AppError) as failure:
        await sign_in.access_token("plan")
    assert failure.value.code == "chatgpt_signed_out"


async def test_a_denied_forged_or_mismatched_callback_signs_nobody_in(tmp_path, monkeypatch):
    fake = OpenAI()
    mock_client(monkeypatch, "chatgpt", fake)
    sign_in = ChatGPTSignIn(tmp_path / "chatgpt.json")
    with pytest.raises(AppError, match="expired or was already used"):
        await sign_in.complete({"state": ["forged"], "code": ["x"]})
    query = parse_qs(urlparse(await sign_in.start("plan", CALLBACK)).query)
    with pytest.raises(AppError, match="cancelled"):
        await sign_in.complete({"state": query["state"], "error": ["access_denied"]})
    # An ID token for another attempt's nonce, or not signed by OpenAI, is not accepted.
    query = parse_qs(urlparse(await sign_in.start("plan", CALLBACK)).query)
    fake.nonce = "another-attempt"
    with pytest.raises(AppError, match="could not be verified"):
        await sign_in.complete(
            {"state": query["state"], "code": ["c"], "client_id": ["oaiapp_test"]}
        )
    query = parse_qs(urlparse(await sign_in.start("plan", CALLBACK)).query)
    fake.nonce = query["nonce"][0]
    forger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    genuine = fake.id_token
    fake.id_token = lambda nonce: jwt.encode(
        jwt.decode(genuine(nonce), options={"verify_signature": False}),
        forger,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(AppError, match="could not be verified"):
        await sign_in.complete(
            {"state": query["state"], "code": ["c"], "client_id": ["oaiapp_test"]}
        )
    assert not (await sign_in.status("plan")).signed_in
    # A later sign-in to a different account does not replace the first one.
    sign_in, fake, _query = await signed_in(tmp_path / "other", monkeypatch)
    query = parse_qs(urlparse(await sign_in.start("plan", CALLBACK)).query)
    fake.nonce, fake.subject = query["nonce"][0], "user-2"
    with pytest.raises(AppError, match="different ChatGPT account"):
        await sign_in.complete({"state": query["state"], "code": ["c"]})


async def test_declined_plan_usage_is_asked_for_again(tmp_path, monkeypatch):
    fake = OpenAI()
    fake.scopes = "openid profile email"
    mock_client(monkeypatch, "chatgpt", fake)
    sign_in = ChatGPTSignIn(tmp_path / "chatgpt.json")
    query = parse_qs(urlparse(await sign_in.start("plan", CALLBACK)).query)
    fake.nonce = query["nonce"][0]
    done = await sign_in.complete(
        {"state": query["state"], "code": ["c"], "client_id": ["oaiapp_test"]}
    )
    assert not done.plan_usage and not (await sign_in.status("plan")).signed_in
    with pytest.raises(AppError) as failure:
        await sign_in.access_token("plan")
    assert failure.value.code == "chatgpt_plan_not_allowed"
    # A returning sign-in skips consent unless asked, so it is asked for.
    again = parse_qs(urlparse(await sign_in.start("plan", CALLBACK)).query)
    assert again["prompt"] == ["consent"] and again["client_id"] == ["oaiapp_test"]
    fake.nonce, fake.scopes = again["nonce"][0], SCOPES
    assert (await sign_in.complete({"state": again["state"], "code": ["c"]})).plan_usage
    assert await sign_in.access_token("plan") == "access-2"


async def test_plan_requests_follow_the_route_rules(monkeypatch):
    preset = Preset(name="P", blocks=[Block(name="rules", template="Be brief.", order=0)])
    compiled = compile_prompt(preset, GlassboxConfig(provider=PLAN), [], "Hi", {}, [])
    body = prepare(PLAN, compiled, [])
    assert "max_output_tokens" not in body and "temperature" not in body
    assert body["input"][0] == {"role": "developer", "content": "Be brief."}
    assert body["store"] is False and body["stream"] is True
    assert any("ChatGPT plan" in item for item in transformations(PLAN))
    with pytest.raises(AppError) as failure:
        prepare(PLAN.model_copy(update={"temperature": 0.5}), compiled, [])
    assert failure.value.code == "provider_temperature"

    sent = []

    def reply(request):
        sent.append(request)
        output = [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]
        return httpx.Response(200, text=events(completed(output)))

    mock_client(monkeypatch, "responses", reply)

    async def on_wire(_body):
        pass

    result = [event async for event in generate(PLAN, compiled, [], body, on_wire, "access-1")]
    assert result[-1]["message"].content == "Hello"
    assert str(sent[0].url) == f"{CHATGPT_API}/responses"
    assert sent[0].headers["authorization"] == "Bearer access-1"


async def test_plan_failures_say_what_to_do(monkeypatch):
    compiled = compile_prompt(Preset(name="P"), GlassboxConfig(provider=PLAN), [], "Hi", {}, [])
    body = prepare(PLAN, compiled, [])

    async def failure_for(handler) -> AppError:
        mock_client(monkeypatch, "responses", handler)
        with pytest.raises(AppError) as failure:

            async def on_wire(_body):
                pass

            async for _event in generate(PLAN, compiled, [], body, on_wire, "access-1"):
                pass
        return failure.value

    limit = {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "x"}}
    error = await failure_for(lambda _r: httpx.Response(429, json=limit))
    assert error.code == "chatgpt_usage_limit" and USAGE_URL in error.message
    failed = {"type": "response.failed", "response": {"error": limit["error"]}}
    error = await failure_for(lambda _r: httpx.Response(200, text=events(failed)))
    assert error.code == "chatgpt_usage_limit"
    error = await failure_for(lambda _r: httpx.Response(401, json={"detail": "denied"}))
    assert error.code == "chatgpt_sign_in"
    unsupported = {
        "error": {"code": "subscription_sharing_unsupported_capability", "param": "tools"}
    }
    error = await failure_for(lambda _r: httpx.Response(400, json=unsupported))
    assert error.code == "chatgpt_unsupported" and "(tools)" in error.message


async def test_the_plan_lists_its_models(monkeypatch):
    models = {
        "models": [
            {"slug": "gpt-a", "display_name": "GPT A", "visibility": "list"},
            {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide"},
        ]
    }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=models)

    mock_client(monkeypatch, "catalog", handler)
    profile = ProviderProfile(
        id="plan", name="Plan", auth="chatgpt", adapter="responses", base_url=CHATGPT_API
    )
    assert await list_models(profile, "access-1") == ["gpt-a"]
    assert requests[0].headers["authorization"] == "Bearer access-1"


async def test_the_browser_finishes_sign_in_here_and_runs_use_the_plan(
    glassbox_client, monkeypatch
):
    client, app = glassbox_client
    fake = OpenAI()
    mock_client(monkeypatch, "chatgpt", fake)
    started = await client.post("/api/v1/chatgpt/plan/sign-in")
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["url"]).query)
    # ChatGPT returns to this server, on the port the browser used.
    assert query["redirect_uri"] == ["http://127.0.0.1:8000/auth/callback"]
    forged = await client.get("/auth/callback", params={"state": "forged", "code": "c"})
    assert forged.status_code == 400 and "did not complete" in forged.text
    assert "script-src 'sha256-" in forged.headers["content-security-policy"]
    fake.nonce = query["nonce"][0]
    page = await client.get(
        "/auth/callback",
        params={"state": query["state"][0], "code": "c", "client_id": "oaiapp_test"},
    )
    assert page.status_code == 200 and "using your ChatGPT plan" in page.text
    assert "access-1" not in page.text
    status = (await client.get("/api/v1/chatgpt/plan")).json()
    assert status == {"signed_in": True, "email": "ada@example.com", "plan_usage": True}

    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    plan = {"id": "plan", "name": "ChatGPT", "auth": "chatgpt", "adapter": "responses"}
    config["providers"].append(plan | {"base_url": CHATGPT_API, "models": ["gpt-x"]})
    config["provider"] = {"profile_id": "plan", "adapter": "responses", "model": "gpt-x"}
    saved = await client.put("/api/v1/config", json=config)
    assert saved.status_code == 200, saved.text
    output = [{"type": "message", "content": [{"type": "output_text", "text": "Done."}]}]
    fake.reply = lambda _r: httpx.Response(200, text=events(completed(output)))
    run, *_ = await start(client, "Hello")
    finished = await wait_state(client, run["id"], {"succeeded", "failed", "interrupted"})
    assert finished["status"] == "succeeded", finished.get("error")
    api = [request for request in fake.requests if request.url.host == "api.openai.com"]
    assert api[-1].headers["authorization"] == "Bearer access-1"

    # A reached usage limit fails the Run with its reason, not as an unknown outcome.
    limit = {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}
    fake.reply = lambda _r: httpx.Response(429, json=limit)
    run, *_ = await start(client, "Again")
    finished = await wait_state(client, run["id"], {"succeeded", "failed", "interrupted"})
    assert finished["status"] == "failed" and finished["stop_reason"] == "chatgpt_usage_limit"
    assert USAGE_URL in finished["error"]

    assert (await client.delete("/api/v1/chatgpt/plan")).json() == {"revoked": True}
    assert not (await client.get("/api/v1/chatgpt/plan")).json()["signed_in"]

    # Removing the provider from Settings forgets its sign-in and registration.
    path = app.state.settings.data_dir / "chatgpt.json"
    assert "plan" in json.loads(path.read_text())["accounts"]
    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    config["providers"] = [item for item in config["providers"] if item["id"] != "plan"]
    config["provider"] = {"profile_id": "simulated", "adapter": "fake", "model": "simulated"}
    assert (await client.put("/api/v1/config", json=config)).status_code == 200
    assert "plan" not in json.loads(path.read_text())["accounts"]
