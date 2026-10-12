# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0
"""Contract tests for the shared Kajenn REST/MCP domain API (no SMTP)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from kajenn import AsgiServer

from kajenn_mail_proxy.mail_proxy_application import MailProxyApplication

ADMIN_TOKEN = "contract-admin"
TOKEN_A = "contract-tenant-a"
TOKEN_B = "contract-tenant-b"
pytestmark = pytest.mark.asyncio


def message(tenant_id="a", message_id="mail-1"):
    return {
        "id": message_id,
        "tenant_id": tenant_id,
        "account_id": "smtp-1",
        "from": "sender@example.test",
        "to": ["recipient@example.test"],
        "subject": "Contract",
        "body": "Never sent",
    }


@pytest.fixture
def core():
    async def tenant_by_token(token):
        return {"id": {TOKEN_A: "a", TOKEN_B: "b"}[token]} if token in (TOKEN_A, TOKEN_B) else None

    async def command(name, data):
        if name == "addMessages":
            return {"ok": True, "queued": len(data["messages"]), "rejected": []}
        if name in ("suspend", "activate"):
            return {"ok": True, "tenant_id": data["tenant_id"], "pending_messages": 0}
        if name == "listAccounts":
            return {"ok": True, "accounts": []}
        return {"ok": True}

    return SimpleNamespace(
        _active=True,
        handle_command=AsyncMock(side_effect=command),
        start=AsyncMock(),
        stop=AsyncMock(),
        db=SimpleNamespace(
            tenants=SimpleNamespace(get_tenant_by_token=AsyncMock(side_effect=tenant_by_token))
        ),
    )


@pytest_asyncio.fixture
async def client(core):
    server = AsgiServer(
        applications=[(MailProxyApplication, {"core": core, "api_token": ADMIN_TOKEN})]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="http://test/mailproxy/"
    ) as client:
        yield client


async def rpc(client, method, params=None, token=ADMIN_TOKEN):
    response = await client.post(
        "mcp",
        headers={"X-API-Token": token},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1
    return body


async def call(client, name, arguments=None, token=ADMIN_TOKEN):
    return await rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token)


def successful(body):
    assert "error" not in body, body
    assert not body["result"].get("isError"), body
    return body["result"]["structuredContent"]


def refused(body):
    assert "error" in body or body.get("result", {}).get("isError") is True, body


async def test_initialize_and_typed_tool_discovery(client):
    initialized = await rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "contract-test", "version": "1"},
        },
    )
    assert "error" not in initialized
    assert "tools" in initialized["result"]["capabilities"]
    tools = (await rpc(client, "tools/list"))["result"]["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    assert {
        "api.status",
        "api.suspend",
        "api.activate",
        "api.enqueue_messages",
        "api.list_accounts",
        "api.create_tenant",
    } <= by_name.keys()
    assert all(not name.startswith("v1.") for name in by_name)
    assert "health" not in by_name and "metrics" not in by_name
    schema = by_name["api.enqueue_messages"]["inputSchema"]
    assert schema["properties"]["messages"]["type"] == "array"
    assert "messages" in schema["required"]
    assert "_request" not in schema["properties"]
    assert "request" not in schema["properties"]


async def test_tenant_discovery_hides_admin_and_call_cannot_bypass_it(client, core):
    tools = (await rpc(client, "tools/list", token=TOKEN_A))["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert "api.enqueue_messages" in names
    assert "api.create_tenant" not in names
    refused(
        await call(
            client, "api.create_tenant", {"payload": {"id": "other", "name": "Other"}}, TOKEN_A
        )
    )
    core.handle_command.assert_not_awaited()


@pytest.mark.parametrize("name", ["health", "v1.status", "v1.commands.add-messages"])
async def test_rest_only_routes_cannot_be_called_as_tools(client, core, name):
    refused(await call(client, name))
    core.handle_command.assert_not_awaited()


@pytest.mark.parametrize("operation", ["suspend", "activate"])
@pytest.mark.parametrize("channel", ["rest", "mcp"])
async def test_tenant_cannot_change_another_tenants_sending(client, core, operation, channel):
    arguments = {"tenant_id": "b", "batch_code": "private-batch"}
    if channel == "mcp":
        refused(await call(client, f"api.{operation}", arguments, TOKEN_A))
    else:
        response = await client.post(
            f"api/{operation}", json=arguments, headers={"X-API-Token": TOKEN_A}
        )
        assert response.status_code in (401, 403), response.text
    core.handle_command.assert_not_awaited()


@pytest.mark.parametrize("channel", ["rest", "mcp"])
@pytest.mark.parametrize("messages", [[message("b")], [message("a"), message("b", "mail-2")]])
async def test_foreign_message_in_batch_prevents_any_enqueue(client, core, channel, messages):
    arguments = {"messages": messages}
    if channel == "mcp":
        refused(await call(client, "api.enqueue_messages", arguments, TOKEN_A))
    else:
        response = await client.post(
            "api/enqueue_messages", json=arguments, headers={"X-API-Token": TOKEN_A}
        )
        assert response.status_code in (401, 403), response.text
    core.handle_command.assert_not_awaited()


async def test_status_has_same_rest_and_mcp_result(client):
    response = await client.get("api/status", headers={"X-API-Token": TOKEN_A})
    assert response.status_code == 200, response.text
    assert successful(await call(client, "api.status", token=TOKEN_A)) == response.json()
    assert response.json()["active"] is True


async def test_enqueue_has_same_rest_and_mcp_result_and_command(client, core):
    arguments = {"messages": [message()], "default_priority": 2}
    response = await client.post(
        "api/enqueue_messages", json=arguments, headers={"X-API-Token": TOKEN_A}
    )
    assert response.status_code == 200, response.text
    rest_command = core.handle_command.await_args
    result = successful(await call(client, "api.enqueue_messages", arguments, TOKEN_A))
    assert result == response.json()
    assert result["queued"] == 1 and result["rejected"] == []
    assert core.handle_command.await_args == rest_command
    assert core.handle_command.await_count == 2
    assert rest_command.args[0] == "addMessages"
    assert rest_command.args[1]["messages"][0]["tenant_id"] == "a"


@pytest.mark.parametrize("token", ["", "unknown-token"])
async def test_anonymous_cannot_enqueue(client, core, token):
    refused(await call(client, "api.enqueue_messages", {"messages": [message()]}, token))
    core.handle_command.assert_not_awaited()
