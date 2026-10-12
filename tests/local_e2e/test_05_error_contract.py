# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Legacy v1 errors retain their HTTP status and FastAPI detail envelope."""

from __future__ import annotations

import pytest

from tests import api_routes

pytestmark = pytest.mark.asyncio


async def test_unknown_account_is_business_400_with_detail(api_client, local_tenants):
    response = await api_client.delete(
        api_routes.account("unknown-error-contract", tenant_id=local_tenants["tenant1"])
    )
    assert response.status_code == 400
    assert "not owned by tenant" in response.json()["detail"]
    assert "error" not in response.json()


@pytest.mark.parametrize(
    "path,payload",
    [
        (api_routes.ACCOUNT, {"id": "incomplete-account"}),
        (api_routes.ADD_MESSAGES, {"messages": [{"id": "incomplete-message"}]}),
    ],
)
async def test_missing_payload_fields_are_422_with_detail(api_client, path, payload):
    response = await api_client.post(path, json=payload)
    assert response.status_code == 422
    assert "detail" in response.json()
    assert "validation error" in response.json()["detail"].lower()


async def test_missing_query_argument_is_422_with_detail(api_client):
    response = await api_client.get(api_routes.accounts())
    assert response.status_code == 422
    assert "detail" in response.json()


async def test_auth_error_preserves_detail_and_challenge(bare_client):
    response = await bare_client.get(api_routes.accounts(), params={"tenant_id": "a"})
    assert response.status_code == 401
    assert "detail" in response.json()
    assert response.headers["www-authenticate"] == "Bearer"
