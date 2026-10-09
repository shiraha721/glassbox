"""List the models a configured provider serves, for the model picker."""

import httpx

from glassbox.domain import AppError, ProviderProfile
from glassbox.infrastructure.providers.errors import error_message, provider_failure

MODEL_LIMIT = 500


async def list_models(profile: ProviderProfile, key: str) -> list[str]:
    """Read model IDs from `GET {base_url}/models`, following Messages API pagination.

    OpenAI-compatible servers return one page of `{"data": [{"id": ...}]}`; the Messages API
    pages with `has_more` and `last_id`. Failures report the provider's own message."""
    if profile.adapter == "anthropic":
        headers = {"anthropic-version": "2023-06-01", **({"x-api-key": key} if key else {})}
    else:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    found: set[str] = set()
    params: dict[str, str | int] = {"limit": 1000} if profile.adapter == "anthropic" else {}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for _ in range(5):
            try:
                response = await client.get(
                    profile.base_url.rstrip("/") + "/models", headers=headers, params=params
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
