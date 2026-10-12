# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0
"""Typed semantic operations shared by REST and MCP.

The v1 groups own command translation and authorization; these methods reuse
those operations without exposing the HTTP-verb dispatch shims as tools.
"""

from __future__ import annotations

from typing import Any

from genro_routes import route

from ..auth_tags import ADMIN_TAG, TENANT_TAG
from ..http_schema import (
    AccountPayload,
    AccountsResponse,
    AddMessagesResponse,
    AddTenantResponse,
    ApiKeyResponse,
    BasicOkResponse,
    CleanupMessagesPayload,
    CleanupMessagesResponse,
    CommandLogResponse,
    DeleteMessagesPayload,
    DeleteMessagesResponse,
    InstanceInfo,
    InstanceUpdatePayload,
    MessagePayload,
    MessagesResponse,
    StatusResponse,
    SuspendResponse,
    TenantInfo,
    TenantPayload,
    TenantsResponse,
    TenantsSyncStatusResponse,
    TenantUpdatePayload,
)
from .account import AccountRoutes, AccountsRoutes
from .base import ProxyRoutes
from .command import CommandsRoutes
from .command_log import CommandLogRoutes
from .instance import InstanceRoutes
from .message import MessagesRoutes
from .tenant import TenantRoutes, TenantsRoutes


class MailApi(ProxyRoutes):
    """One typed method per operation, independently discoverable by role."""

    def __init__(self, application):
        super().__init__(application)
        self.commands = CommandsRoutes(application)
        self.account = AccountRoutes(application)
        self.accounts = AccountsRoutes(application)
        self.messages = MessagesRoutes(application)
        self.tenant = TenantRoutes(application)
        self.tenants = TenantsRoutes(application)
        self.instance = InstanceRoutes(application)
        self.command_log = CommandLogRoutes(application)

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="get")
    async def status(self) -> StatusResponse:
        """Read whether mail dispatch is active."""
        return StatusResponse(ok=True, active=self.core._active).model_dump(exclude_none=True)

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def run_now(self, _request=None) -> BasicOkResponse:
        """Wake dispatch for the authenticated tenant, or all tenants for admin."""
        return await self.commands.run_now(request=_request)

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def suspend(
        self, tenant_id: str, batch_code: str | None = None, _request=None
    ) -> SuspendResponse:
        """Suspend one tenant or one batch."""
        return await self.commands.suspend(
            request=_request, tenant_id=tenant_id, batch_code=batch_code
        )

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def activate(
        self, tenant_id: str, batch_code: str | None = None, _request=None
    ) -> SuspendResponse:
        """Resume one tenant or one batch."""
        return await self.commands.activate(
            request=_request, tenant_id=tenant_id, batch_code=batch_code
        )

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def enqueue_messages(
        self, messages: list[MessagePayload], default_priority: int | None = None, _request=None
    ) -> AddMessagesResponse:
        """Queue messages without waiting for SMTP delivery."""
        body = {
            "messages": [item.model_dump(by_alias=True, exclude_none=True) for item in messages]
        }
        if default_priority is not None:
            body["default_priority"] = default_priority
        return await self.commands.add_messages(request=_request, body_data=body)

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def delete_messages(
        self, tenant_id: str, payload: DeleteMessagesPayload, _request=None
    ) -> DeleteMessagesResponse:
        """Delete selected messages belonging to a tenant."""
        return await self.commands.delete_messages(
            request=_request, tenant_id=tenant_id, body_data=payload.model_dump()
        )

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def cleanup_messages(
        self, tenant_id: str, payload: CleanupMessagesPayload, _request=None
    ) -> CleanupMessagesResponse:
        """Remove expired message records for a tenant."""
        return await self.commands.cleanup_messages(
            request=_request, tenant_id=tenant_id, body_data=payload.model_dump()
        )

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="get")
    async def list_messages(
        self,
        tenant_id: str,
        active_only: bool = False,
        include_history: bool = False,
        _request=None,
    ) -> MessagesResponse:
        """Read a tenant's queue and optional delivery history."""
        return await self.messages.index(
            request=_request,
            tenant_id=tenant_id,
            active_only=active_only,
            include_history=include_history,
        )

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="get")
    async def list_accounts(self, tenant_id: str, _request=None) -> AccountsResponse:
        """List SMTP accounts belonging to a tenant."""
        return await self.accounts.index(request=_request, tenant_id=tenant_id)

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def add_account(self, payload: AccountPayload, _request=None) -> BasicOkResponse:
        """Register or update an SMTP account."""
        return await self.account.add_account(_request, payload.model_dump())

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def delete_account(
        self, account_id: str, tenant_id: str, _request=None
    ) -> BasicOkResponse:
        """Delete one tenant's SMTP account."""
        return await self.account.delete_account(_request, account_id, tenant_id)

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="post")
    async def create_tenant(self, payload: TenantPayload, _request=None) -> AddTenantResponse:
        """Register a tenant and issue its initial API key."""
        return await self.tenant.add_tenant(_request, payload.model_dump())

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="get")
    async def get_tenant(self, tenant_id: str, _request=None) -> TenantInfo:
        """Read a tenant's configuration."""
        return await self.tenant.get_tenant(_request, tenant_id)

    @route(channel_channels="mcp,rest", auth_rule=TENANT_TAG, openapi_method="post")
    async def update_tenant(
        self, tenant_id: str, payload: TenantUpdatePayload, _request=None
    ) -> BasicOkResponse:
        """Update a tenant's configuration."""
        return await self.tenant.update_tenant(
            _request, tenant_id, payload.model_dump(exclude_none=True)
        )

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="post")
    async def delete_tenant(self, tenant_id: str, _request=None) -> BasicOkResponse:
        """Delete a tenant and its resources."""
        return await self.tenant.delete_tenant(_request, tenant_id)

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="post")
    async def create_api_key(self, tenant_id: str, _request=None) -> ApiKeyResponse:
        """Replace a tenant's API key with a newly generated key."""
        return await self.tenant.create_api_key(_request, tenant_id)

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="post")
    async def revoke_api_key(self, tenant_id: str, _request=None) -> BasicOkResponse:
        """Revoke a tenant's API key."""
        return await self.tenant.revoke_api_key(_request, tenant_id)

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="get")
    async def list_tenants(self, active_only: bool = False) -> TenantsResponse:
        """List tenants, optionally restricting to active tenants."""
        return await self.tenants.index(active_only=active_only)

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="get")
    async def sync_status(self) -> TenantsSyncStatusResponse:
        """Read synchronization status for all tenants."""
        return await self.tenants.sync_status()

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="get")
    async def get_instance(self) -> InstanceInfo:
        """Read instance settings without the bounce IMAP password."""
        return await self.instance.get_instance()

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="post")
    async def update_instance(self, payload: InstanceUpdatePayload) -> BasicOkResponse:
        """Update instance settings."""
        return await self.instance.update_instance(payload.model_dump(exclude_none=True))

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="post")
    async def reload_bounce(self) -> BasicOkResponse:
        """Reload the bounce receiver using persisted configuration."""
        return await self.instance.reload_bounce()

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="get")
    async def list_commands(
        self,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
        endpoint_filter: str | None = None,
        limit: int = 100,
    ) -> CommandLogResponse:
        """Read a filtered page from the command audit trail."""
        return await self.command_log.index(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
            endpoint_filter=endpoint_filter,
            limit=limit,
        )

    @route(channel_channels="mcp,rest", auth_rule=ADMIN_TAG, openapi_method="get")
    async def export_commands(
        self, tenant_id: str | None = None, since_ts: int | None = None, until_ts: int | None = None
    ) -> dict[str, Any]:
        """Export commands in their replay format."""
        return await self.command_log.export(
            tenant_id=tenant_id, since_ts=since_ts, until_ts=until_ts
        )
