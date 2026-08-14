import pytest

from app.config import settings
from app.utils.turnstile import TurnstileError, validate_turnstile


async def test_not_enforced_passes(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_enforced", False)
    assert await validate_turnstile(None) is True


async def test_enforced_without_token_raises(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_enforced", True)
    monkeypatch.setattr(settings, "turnstile_secret_key", "test-secret")
    with pytest.raises(TurnstileError):
        await validate_turnstile(None)


async def test_enforced_without_secret_raises(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_enforced", True)
    monkeypatch.setattr(settings, "turnstile_secret_key", "")
    with pytest.raises(TurnstileError, match="not configured"):
        await validate_turnstile("some-token")