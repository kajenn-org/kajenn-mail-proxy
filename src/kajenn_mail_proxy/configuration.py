# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Application-owned configuration; recipes and constructor overrides are validated."""

from __future__ import annotations

from genro_bag import BagResolver
from genro_builders.builder import element
from kajenn.application import ApplicationGrammar
from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngineSettings(Settings):
    start_active: bool = True
    result_queue_size: int = 1000
    message_queue_size: int = 10000
    queue_put_timeout: float = 5.0
    max_enqueue_batch: int = 1000
    attachment_timeout: int = 30
    client_sync_url: str | None = None
    client_sync_user: str | None = None
    client_sync_password: str | None = None
    client_sync_token: str | None = None
    default_priority: int | str = 2
    send_loop_interval: float = 0.5
    report_retention_seconds: int | None = None
    batch_size_per_account: int = 50
    test_mode: bool = False
    log_delivery_activity: bool = False
    max_retries: int | None = None
    retry_delays: list[int] | None = None
    max_concurrent_sends: int = 10
    max_concurrent_per_account: int = 3
    max_concurrent_attachments: int = 3


class CacheSettings(Settings):
    memory_max_mb: float = 50.0
    memory_ttl_seconds: int = 300
    disk_dir: str | None = None
    disk_max_mb: float = 500.0
    disk_ttl_seconds: int = 3600
    disk_threshold_kb: float = 100.0


class BounceSettings(Settings):
    enabled: bool = False
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_ssl: bool = True
    poll_interval: int = 60


class MailProxyGrammar(ApplicationGrammar):
    """Deployment settings of the mail application."""

    @element(node_label="database")
    def database(self, path: str | BagResolver = "./mail_service.db") -> None:
        """Persistent database location."""

    @element(node_label="engine")
    def engine(
        self,
        *,
        start_active: bool | BagResolver = True,
        result_queue_size: int | BagResolver = 1000,
        message_queue_size: int | BagResolver = 10000,
        queue_put_timeout: float | BagResolver = 5.0,
        max_enqueue_batch: int | BagResolver = 1000,
        attachment_timeout: int | BagResolver = 30,
        client_sync_url: str | None | BagResolver = None,
        client_sync_user: str | None | BagResolver = None,
        client_sync_password: str | None | BagResolver = None,
        client_sync_token: str | None | BagResolver = None,
        default_priority: int | str | BagResolver = 2,
        send_loop_interval: float | BagResolver = 0.5,
        report_retention_seconds: int | None | BagResolver = None,
        batch_size_per_account: int | BagResolver = 50,
        test_mode: bool | BagResolver = False,
        log_delivery_activity: bool | BagResolver = False,
        max_retries: int | None | BagResolver = None,
        retry_delays: list[int] | None | BagResolver = None,
        max_concurrent_sends: int | BagResolver = 10,
        max_concurrent_per_account: int | BagResolver = 3,
        max_concurrent_attachments: int | BagResolver = 3,
    ) -> None:
        """Engine settings."""

    @element(node_label="cache")
    def cache(
        self,
        *,
        memory_max_mb: float | BagResolver = 50.0,
        memory_ttl_seconds: int | BagResolver = 300,
        disk_dir: str | None | BagResolver = None,
        disk_max_mb: float | BagResolver = 500.0,
        disk_ttl_seconds: int | BagResolver = 3600,
        disk_threshold_kb: float | BagResolver = 100.0,
    ) -> None:
        """Cache settings."""

    @element(node_label="bounce")
    def bounce(
        self,
        *,
        enabled: bool | BagResolver = False,
        imap_host: str | None | BagResolver = None,
        imap_port: int | BagResolver = 993,
        imap_user: str | BagResolver = "",
        imap_password: str | BagResolver = "",
        imap_ssl: bool | BagResolver = True,
        poll_interval: int | BagResolver = 60,
    ) -> None:
        """Bounce settings."""


def read_settings(application, section, model, overrides):
    values = {
        name: application.config(f"{section}.{name}", default=field.default)
        for name, field in model.model_fields.items()
    }
    values.update(overrides)
    return model.model_validate(values)
