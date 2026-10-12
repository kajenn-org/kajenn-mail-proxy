# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Mail proxy database manager with pre-registered tables.

Synchronous Asqueel store with mail-proxy specific tables and high-level operations.

Example:
    db = MailStore("/data/mail.db")
    db.init_db()

    # High-level operations
    db.add_tenant({"id": "acme", "name": "ACME Corp"})
    tenant = db.get_tenant("acme")

    db.add_account({"id": "smtp1", "host": "smtp.example.com", "port": 587})

    db.insert_messages([{"id": "msg1", "payload": {...}}])
    ready = db.fetch_ready_messages(limit=10, now_ts=time.time())
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any

from ..entities import (
    AccountsTable,
    CommandLogTable,
    InstanceConfigTable,
    InstanceTable,
    MessageEventTable,
    MessagesTable,
    SendLogTable,
    TenantsTable,
)
from .backend import SyncSqlStore


class MailStore(SyncSqlStore):
    """Mail proxy database with pre-registered tables.

    Provides high-level operations delegating to table managers.
    Backward compatible with the old Persistence API.
    """

    def __init__(self, connection_string: str = "/data/mail_service.db"):
        """Initialize the mail proxy database.

        Args:
            connection_string: Database connection string. Formats:
                - "/path/to/db.sqlite" - SQLite file
                - ":memory:" - SQLite in-memory
                - "sqlite:/path/to/db" - SQLite explicit
                - "postgresql://user:pass@host/db" - PostgreSQL
        """
        super().__init__(connection_string)
        self.db_path = connection_string  # Backward compatibility

        # Register all tables
        self.add_table(TenantsTable)
        self.add_table(AccountsTable)
        self.add_table(MessagesTable)
        self.add_table(MessageEventTable)
        self.add_table(SendLogTable)
        self.add_table(CommandLogTable)
        self.add_table(InstanceConfigTable)
        self.add_table(InstanceTable)

    @property
    def tenants(self) -> TenantsTable:
        return self.table("tenants")  # type: ignore[return-value]

    @property
    def accounts(self) -> AccountsTable:
        return self.table("accounts")  # type: ignore[return-value]

    @property
    def messages(self) -> MessagesTable:
        return self.table("messages")  # type: ignore[return-value]

    @property
    def send_log(self) -> SendLogTable:
        return self.table("send_log")  # type: ignore[return-value]

    @property
    def message_events(self) -> MessageEventTable:
        return self.table("message_events")  # type: ignore[return-value]

    @property
    def config(self) -> InstanceConfigTable:
        return self.table("instance_config")  # type: ignore[return-value]

    @property
    def instance(self) -> InstanceTable:
        return self.table("instance")  # type: ignore[return-value]

    @property
    def command_log(self) -> CommandLogTable:
        return self.table("command_log")  # type: ignore[return-value]

    def init_db(self) -> None:
        """Initialize database: connect, create schema, run migrations.

        After creating tables, sync_schema() is called on each table to add
        any columns that may be missing from older database versions. This
        enables automatic schema migration when new columns are added.

        Special migrations (like messages pk change from INTEGER to UUID)
        are handled separately before sync_schema.
        """
        self.connect()
        self.check_structure()

        # Run legacy schema migrations before sync_schema
        # This handles the messages.pk INTEGER->UUID migration for pre-0.6.5 databases
        if self.messages.migrate_from_legacy_schema():
            import logging

            logging.getLogger("kajenn_mail_proxy").info(
                "Migrated messages table from legacy schema (INTEGER pk -> UUID pk)"
            )

        # Sync schema for all tables - adds any missing columns automatically
        self.tenants.sync_schema()
        self.accounts.sync_schema()
        self.messages.sync_schema()
        self.message_events.sync_schema()
        self.send_log.sync_schema()
        self.command_log.sync_schema()

    # -------------------------------------------------------------------------
    # Tenants
    # -------------------------------------------------------------------------
    def add_tenant(self, tenant: dict[str, Any]) -> str:
        """Add or update a tenant. Returns API key for new tenants."""
        return self.tenants.add(tenant)

    def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return self.tenants.get(tenant_id)

    def list_tenants(self, active_only: bool = False) -> list[dict[str, Any]]:
        return self.tenants.list_all(active_only)

    def update_tenant(self, tenant_id: str, updates: dict[str, Any]) -> bool:
        return self.tenants.update_fields(tenant_id, updates)

    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete tenant and cascade to accounts/messages."""
        accs = self.accounts.select(columns=["id"], where={"tenant_id": tenant_id})
        for acc in accs:
            account_id = acc["id"]
            self.purge_messages_for_account(account_id, tenant_id)
            self.accounts.remove(tenant_id, account_id)
            if not self.accounts.select_one(where={"id": account_id}):
                self.send_log.purge_for_account(account_id)
        return self.tenants.remove(tenant_id)

    # -------------------------------------------------------------------------
    # Accounts
    # -------------------------------------------------------------------------
    def add_account(self, acc: dict[str, Any]) -> None:
        self.accounts.add(acc)

    def add_pec_account(self, acc: dict[str, Any]) -> None:
        """Add a PEC account with IMAP configuration."""
        self.accounts.add_pec_account(acc)

    def list_accounts(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        return self.accounts.list_all(tenant_id)

    def list_pec_accounts(self) -> list[dict[str, Any]]:
        """Return all PEC accounts."""
        return self.accounts.list_pec_accounts()

    def delete_account(self, tenant_id: str, account_id: str) -> None:
        """Delete an account and its related messages/logs.

        Args:
            tenant_id: The tenant that owns this account.
            account_id: The account identifier.
        """
        self.purge_messages_for_account(account_id, tenant_id)
        self.accounts.remove(tenant_id, account_id)
        if not self.accounts.select_one(where={"id": account_id}):
            self.send_log.purge_for_account(account_id)

    def get_account(self, tenant_id: str, account_id: str) -> dict[str, Any]:
        """Get an account by tenant and account ID.

        Args:
            tenant_id: The tenant that owns this account.
            account_id: The account identifier.

        Raises:
            ValueError: If account not found for this tenant.
        """
        return self.accounts.get(tenant_id, account_id)

    def update_imap_sync_state(
        self,
        tenant_id: str,
        account_id: str,
        last_uid: int,
        uidvalidity: int | None = None,
    ) -> None:
        """Update IMAP sync state after processing PEC receipts."""
        self.accounts.update_imap_sync_state(tenant_id, account_id, last_uid, uidvalidity)

    def get_pec_account_ids(self) -> set[str]:
        """Return set of account IDs that are PEC accounts."""
        pec_accounts = self.accounts.list_pec_accounts()
        return {acc["id"] for acc in pec_accounts}

    # -------------------------------------------------------------------------
    # Messages
    # -------------------------------------------------------------------------
    def insert_messages(
        self, entries: Sequence[dict[str, Any]], auto_pec: bool = True
    ) -> list[dict[str, str]]:
        """Insert messages into the queue.

        Args:
            entries: List of message entries to insert.
            auto_pec: If True, automatically set is_pec=1 for messages
                sent via PEC accounts.

        Returns:
            List of {"id": msg_id, "pk": pk} for inserted messages.
        """
        pec_account_ids = self.get_pec_account_ids() if auto_pec else None
        return self.messages.insert_batch(entries, pec_account_ids)

    def fetch_ready_messages(
        self,
        *,
        limit: int,
        now_ts: int,
        priority: int | None = None,
        min_priority: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.messages.fetch_ready(
            limit=limit, now_ts=now_ts, priority=priority, min_priority=min_priority
        )

    def set_deferred(self, pk: str, deferred_ts: int, reason: str | None = None) -> None:
        """Mark message as deferred for retry.

        Args:
            pk: Internal primary key for the update operation (UUID string).
            deferred_ts: Timestamp when message can be retried.
            reason: Optional reason for deferral.
        """
        self.messages.set_deferred(pk, deferred_ts)
        # Record deferred event for reporting
        self.message_events.add_event(
            message_pk=pk,
            event_type="deferred",
            event_ts=deferred_ts,
            description=reason,
        )

    def clear_deferred(self, pk: str) -> None:
        """Clear the deferred timestamp for a message.

        Args:
            pk: Internal primary key of the message (UUID string).
        """
        self.messages.clear_deferred(pk)

    def mark_sent(self, pk: str, smtp_ts: int) -> None:
        """Mark message as successfully sent.

        Args:
            pk: Internal primary key for the update operation (UUID string).
            smtp_ts: Timestamp when SMTP send was attempted.
        """
        self.messages.mark_sent(pk, smtp_ts)
        self.message_events.add_event(
            message_pk=pk,
            event_type="sent",
            event_ts=smtp_ts,
        )

    def mark_error(self, pk: str, smtp_ts: int, error: str) -> None:
        """Mark message as failed with error.

        Args:
            pk: Internal primary key for the update operation (UUID string).
            smtp_ts: Timestamp when SMTP send was attempted.
            error: Error description.
        """
        self.messages.mark_error(pk, smtp_ts)
        self.message_events.add_event(
            message_pk=pk,
            event_type="error",
            event_ts=smtp_ts,
            description=error,
        )

    def update_message_payload(self, pk: str, payload: dict[str, Any]) -> None:
        """Update the payload field of a message.

        Args:
            pk: Internal primary key of the message (UUID string).
            payload: New payload data.
        """
        self.messages.update_payload(pk, payload)

    def clear_pec_flag(self, pk: str) -> None:
        """Clear is_pec flag when recipient is not a PEC address.

        Args:
            pk: Internal primary key of the message (UUID string).
        """
        self.messages.clear_pec_flag(pk)

    def get_pec_messages_without_acceptance(self, cutoff_ts: int) -> list[dict[str, Any]]:
        """Get PEC messages sent before cutoff without acceptance receipt."""
        return self.messages.get_pec_without_acceptance(cutoff_ts)

    def get_message(self, msg_id: str, tenant_id: str) -> dict[str, Any] | None:
        """Get a message by ID within a tenant.

        Args:
            msg_id: Client-provided message ID.
            tenant_id: Tenant ID (required for multi-tenant isolation).
        """
        return self.messages.get(msg_id, tenant_id)

    def delete_message(self, msg_id: str, tenant_id: str) -> bool:
        """Delete a message and all its events (frontier method).

        Args:
            msg_id: Client-provided message ID.
            tenant_id: Tenant ID (required for multi-tenant isolation).
        """
        # Resolve (tenant_id, id) → pk, then delete by pk
        msg = self.messages.get(msg_id, tenant_id)
        if not msg:
            return False
        return self.delete_message_by_pk(msg["pk"])

    def delete_message_by_pk(self, pk: str) -> bool:
        """Delete a message and all its events by internal primary key.

        Args:
            pk: Internal primary key (UUID string).
        """
        self.message_events.delete_for_message(pk)
        return self.messages.remove_by_pk(pk)

    def purge_messages_for_account(self, account_id: str, tenant_id: str | None = None) -> None:
        """Delete all messages and their events for an account."""
        # Get all message pks for this account and delete their events
        messages = self.messages.select(
            columns=["pk"],
            where={
                "account_id": account_id,
                **({"tenant_id": tenant_id} if tenant_id is not None else {}),
            },
        )
        for msg in messages:
            self.message_events.delete_for_message(msg["pk"])
        self.messages.purge_for_account(account_id, tenant_id)

    def existing_message_ids(self, ids: Iterable[str]) -> set[str]:
        return self.messages.existing_ids(ids)

    def mark_bounced(
        self,
        pk: str,
        bounce_type: str,
        bounce_code: str | None = None,
        bounce_reason: str | None = None,
        bounce_ts: int | None = None,
    ) -> None:
        """Record a bounce event for a message.

        Args:
            pk: Internal primary key of the message (UUID string).
            bounce_type: Type of bounce (hard, soft).
            bounce_code: SMTP error code (e.g., "550").
            bounce_reason: Reason for the bounce.
            bounce_ts: Timestamp of the bounce. Defaults to current time.
        """
        event_ts = bounce_ts if bounce_ts is not None else int(time.time())
        self.message_events.add_event(
            message_pk=pk,
            event_type="bounce",
            event_ts=event_ts,
            description=bounce_reason,
            metadata={"bounce_type": bounce_type, "bounce_code": bounce_code},
        )

    def remove_fully_reported_before(self, threshold_ts: int) -> int:
        """Delete messages whose all events have been reported before threshold."""
        return self.messages.remove_fully_reported_before(threshold_ts)

    def remove_fully_reported_before_for_tenant(self, threshold_ts: int, tenant_id: str) -> int:
        """Delete fully reported messages older than threshold for a tenant."""
        return self.messages.remove_fully_reported_before_for_tenant(threshold_ts, tenant_id)

    def list_messages(
        self,
        tenant_id: str,
        *,
        active_only: bool = False,
        include_history: bool = False,
    ) -> list[dict[str, Any]]:
        """List messages for a specific tenant.

        Args:
            tenant_id: Required tenant ID for multi-tenant isolation.
            active_only: If True, only return messages pending delivery.
            include_history: If True, include event history for each message.

        Raises:
            ValueError: If tenant_id is not provided.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for list_messages")
        return self.messages.list_all(
            tenant_id=tenant_id,
            active_only=active_only,
            include_history=include_history,
        )

    def count_active_messages(self) -> int:
        return self.messages.count_active()

    def count_pending_messages(self, tenant_id: str, batch_code: str | None = None) -> int:
        """Count pending messages for a tenant, optionally filtered by batch_code."""
        return self.messages.count_pending_for_tenant(tenant_id, batch_code)

    # -------------------------------------------------------------------------
    # Send log
    # -------------------------------------------------------------------------
    def log_send(self, account_id: str, timestamp: int) -> None:
        self.send_log.log(account_id, timestamp)

    def count_sends_since(self, account_id: str, since_ts: int) -> int:
        return self.send_log.count_since(account_id, since_ts)

    # -------------------------------------------------------------------------
    # Message events
    # -------------------------------------------------------------------------
    def add_event(
        self,
        message_pk: str,
        event_type: str,
        event_ts: int,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a message event (sent, error, deferred, bounce, pec_*).

        Args:
            message_pk: Internal primary key of the message (UUID string).
            event_type: Type of event.
            event_ts: Unix timestamp when the event occurred.
            description: Optional description.
            metadata: Optional extra data.
        """
        return self.message_events.add_event(
            message_pk, event_type, event_ts, description, metadata
        )

    def fetch_unreported_events(self, limit: int) -> list[dict[str, Any]]:
        """Fetch events not yet reported to clients."""
        return self.message_events.fetch_unreported(limit)

    def mark_events_reported(self, event_ids: list[int], reported_ts: int) -> None:
        """Mark events as reported to client."""
        self.message_events.mark_reported(event_ids, reported_ts)

    def get_events_for_message(self, message_pk: str) -> list[dict[str, Any]]:
        """Get all events for a specific message.

        Args:
            message_pk: Internal primary key of the message (UUID string).
        """
        return self.message_events.get_events_for_message(message_pk)

    def delete_events_for_message(self, message_pk: str) -> int:
        """Delete all events for a message. Returns deleted count.

        Args:
            message_pk: Internal primary key of the message (UUID string).
        """
        return self.message_events.delete_for_message(message_pk)

    # -------------------------------------------------------------------------
    # Instance config
    # -------------------------------------------------------------------------
    def get_config(self, key: str, default: str | None = None) -> str | None:
        return self.config.get(key, default)

    def set_config(self, key: str, value: str) -> None:
        self.config.set(key, value)

    def get_all_config(self) -> dict[str, str]:
        return self.config.get_all()

    # -------------------------------------------------------------------------
    # Command log (audit trail)
    # -------------------------------------------------------------------------
    def log_command(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        tenant_id: str | None = None,
        response_status: int | None = None,
        response_body: dict[str, Any] | None = None,
    ) -> int:
        """Record an API command for audit trail."""
        return self.command_log.log_command(
            endpoint=endpoint,
            payload=payload,
            tenant_id=tenant_id,
            response_status=response_status,
            response_body=response_body,
        )

    def list_commands(
        self,
        *,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
        endpoint_filter: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List logged commands with optional filters."""
        return self.command_log.list_commands(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
            endpoint_filter=endpoint_filter,
            limit=limit,
        )

    def export_commands(
        self,
        *,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Export commands in replay-friendly format."""
        return self.command_log.export_commands(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
        )

    def purge_commands_before(self, threshold_ts: int) -> int:
        """Delete command logs older than threshold."""
        return self.command_log.purge_before(threshold_ts)


# Backward compatibility alias
Persistence = MailStore

__all__ = ["MailStore", "Persistence"]
