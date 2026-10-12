# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Authentication defaults, explicit legacy opt-in and trusted Kajenn identities."""

from __future__ import annotations

import pytest
from kajenn import Avatar

from kajenn_mail_proxy.auth_tags import ADMIN_IDENTITY, ADMIN_TAG, TENANT_TAG
from kajenn_mail_proxy.mail_proxy_application import MailProxyApplication

API_TOKEN = "secret-token"


class DummyCore:
    """The smallest engine the application accepts: it is never called here."""

    def __init__(self):
        self._active = True


def _scope(token: str | None = None) -> dict:
    """An http scope carrying the ``X-API-Token`` header, or carrying none."""
    headers = [(b"x-api-token", token.encode())] if token else []
    return {"type": "http", "headers": headers}


def _application(api_token: str | None, **kwargs) -> MailProxyApplication:
    return MailProxyApplication(core=DummyCore(), api_token=api_token, **kwargs)


class TestNoTokenConfigured:
    @pytest.mark.asyncio
    async def test_a_request_without_a_token_is_anonymous(self):
        assert await _application(None).authenticate_request(_scope()) is None

    @pytest.mark.asyncio
    async def test_explicit_legacy_opt_in_allows_anonymous_admin(self):
        avatar = await _application(None, allow_anonymous_admin=True).authenticate_request(_scope())
        assert avatar.identity == ADMIN_IDENTITY
        assert set(avatar.tags) == {ADMIN_TAG, TENANT_TAG}

    @pytest.mark.asyncio
    async def test_kajenn_admin_identity_gets_mail_admin_permissions(self):
        scope = _scope()
        scope["auth"] = Avatar("kajenn-admin", ["admin"])
        avatar = await _application(None).authenticate_request(scope)
        assert avatar.identity == "kajenn-admin"
        assert {ADMIN_TAG, TENANT_TAG} <= set(avatar.tags)

    @pytest.mark.asyncio
    async def test_kajenn_user_is_not_promoted_to_admin(self):
        scope = _scope()
        scope["auth"] = Avatar("ordinary-user", ["user"])
        avatar = await _application(None).authenticate_request(scope)
        assert avatar.identity == "ordinary-user"
        assert ADMIN_TAG not in avatar.tags


class TestTokenConfigured:
    """With a token configured, only that token is the admin identity."""

    @pytest.mark.asyncio
    async def test_the_configured_token_is_admin(self):
        avatar = await _application(API_TOKEN).authenticate_request(_scope(API_TOKEN))
        assert avatar is not None
        assert avatar.identity == ADMIN_IDENTITY

    @pytest.mark.asyncio
    async def test_no_token_is_anonymous(self):
        assert await _application(API_TOKEN).authenticate_request(_scope()) is None
