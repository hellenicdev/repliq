"""Cloudflare Turnstile server-side verification."""

import httpx

from ..config import settings

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileError(Exception):
    pass


async def validate_turnstile(token: str | None, remote_ip: str | None = None) -> bool:
    """Verify a Turnstile widget token. Raises TurnstileError with a clear
    message when the challenge is invalid or misconfigured."""
    if not settings.turnstile_enforced:
        return True

    if not settings.turnstile_secret_key:
        raise TurnstileError(
            "TURNSTILE_SECRET_KEY is not configured. Add it to backend/.env "
            "(Cloudflare Dashboard -> Turnstile)."
        )
    if not token:
        raise TurnstileError("Turnstile challenge not completed.")

    payload = {"secret": settings.turnstile_secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(SITEVERIFY_URL, data=payload)

    data = resp.json()
    if data.get("success"):
        return True
    codes = data.get("error-codes", [])
    raise TurnstileError(f"Turnstile verification failed: {', '.join(codes) or 'unknown error'}")
