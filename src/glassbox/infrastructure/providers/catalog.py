"""List the models a configured provider serves, for the model picker."""

import httpx

from glassbox.domain import CHATGPT_API, AppError, ProviderProfile, RequestFormat
from glassbox.infrastructure.providers import USER_AGENT
from glassbox.infrastructure.providers.errors import error_message, plan_failure, provider_failure

MODEL_LIMIT = 500


async def list_models(profile: ProviderProfile, key: str) -> list[str]:
    """Read model IDs from a provider's `GET /models`, following Messages API pagination.

    OpenAI-compatible servers return one page of `{"data": [{"id": ...}]}`; the Messages API
    pages with `has_more` and `last_id`. A service with several request formats is asked at
    its Chat Completions or Responses endpoint, since compatible services serve that list
    more often than Anthropic's. With a ChatGPT sign-in the API lists the plan's models as
    `{"models": [{"slug", "visibility"}]}`, of which those marked `list` are offered. Failures
    report the provider's own message."""
    if profile.auth == "chatgpt":
        return await plan_models(key)
    offered = {profile.adapter, *profile.endpoints}
    listed: RequestFormat = (
        "openai" if "openai" in offered else "responses" if "responses" in offered else "anthropic"
    )
    anthropic = listed == "anthropic"
    headers = {"User-Agent": USER_AGENT}
    if anthropic:
        headers.update({"anthropic-version": "2023-06-01", **({"x-api-key": key} if key else {})})
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    found: set[str] = set()
    params: dict[str, str | int] = {"limit": 1000} if anthropic else {}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for _ in range(5):
            try:
                response = await client.get(
                    profile.endpoint(listed).rstrip("/") + "/models", headers=headers, params=params
                )
            except httpx.TimeoutException:
                raise provider_failure("the model list request timed out.") from None
            except httpx.TransportError:
                raise provider_failure("could not connect to the configured base URL.") from None
            try:
                body = response.json()
            except ValueError:
                body = None
            if response.status_code != 200:
                detail = error_message(body) or response.text[:300]
                raise provider_failure(detail, key, response.status_code)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise AppError("provider_protocol", "The provider returned no model list.", 502)
            found.update(
                item["id"]
                for item in body["data"]
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
            )
            if not body.get("has_more") or not body.get("last_id"):
                break
            params = {"limit": 1000, "after_id": body["last_id"]}
    return sorted(found)[:MODEL_LIMIT]


async def plan_models(token: str) -> list[str]:
    """The models a ChatGPT sign-in may use, as the plan lists them."""
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = await client.get(CHATGPT_API + "/models", headers=headers)
    except httpx.TimeoutException:
        raise provider_failure("the model list request timed out.") from None
    except httpx.TransportError:
        raise provider_failure("could not connect to the OpenAI API.") from None
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200:
        if failure := plan_failure(body, response.status_code):
            raise failure
        raise provider_failure(
            error_message(body) or response.text[:300], token, response.status_code
        )
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        raise AppError("provider_protocol", "ChatGPT returned no model list.", 502)
    return [
        item["slug"]
        for item in body["models"][:MODEL_LIMIT]
        if isinstance(item, dict)
        and item.get("visibility") == "list"
        and isinstance(item.get("slug"), str)
        and item["slug"].strip()
    ]
