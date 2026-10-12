# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Backend selection stays lazy and accepts supported deployment locations."""

from __future__ import annotations

import pytest

from kajenn_mail_proxy.persistence.backend import SyncSqlStore


@pytest.mark.parametrize(
    "location", ["./mail.db", "/tmp/mail.db", "sqlite:/tmp/mail.db", ":memory:", "sqlite::memory:"]
)
def test_sqlite_selection_does_not_construct_runtime(location):
    store = SyncSqlStore(location)
    try:
        assert store.backend == "sqlite"
        assert store.schema == "main"
        assert store._runtime is None
        assert "INTEGER PRIMARY KEY" in store.adapter.pk_column("id")
        if ":memory:" not in location:
            assert store.location == location.removeprefix("sqlite:")
    finally:
        store.dispose()


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_postgresql_selection_normalizes_alias_without_connecting(scheme):
    store = SyncSqlStore(f"{scheme}://user:secret@localhost:5432/mail")
    assert store.backend == "postgresql"
    assert store.schema == "public"
    assert store.location == "postgresql://user:secret@localhost:5432/mail"
    assert store._runtime is None
    assert "SERIAL PRIMARY KEY" in store.adapter.pk_column("id")
    assert store.adapter._sql_name("user") == '"user"'


@pytest.mark.parametrize(
    "location", ["mysql://localhost/mail", "oracle://localhost/mail", "redis://localhost/mail"]
)
def test_unsupported_database_scheme_is_rejected(location):
    with pytest.raises(ValueError):
        SyncSqlStore(location)


@pytest.mark.parametrize(
    "location,schema",
    [("sqlite:/tmp/model-only.db", "main"), ("postgresql://user:secret@localhost/mail", "public")],
)
def test_resolved_asqueel_model_preserves_domain_tables_and_keys(location, schema):
    from kajenn_mail_proxy.asqueel_db import MailDatabase

    if schema == "public":
        pytest.importorskip("psycopg")
    runtime = MailDatabase(location)
    model = runtime.model
    expected = {
        "tenants",
        "accounts",
        "messages",
        "message_events",
        "send_log",
        "command_log",
        "instance_config",
        "instance",
    }
    assert set(model.tables) == {f"{schema}.{name}" for name in expected}
    assert model.table(f"{schema}.accounts").pkey == ("tenant_id", "id")
    messages = model.table(f"{schema}.messages")
    assert messages.pkey == ("pk",)
    constraints = messages.attributes.get("constraints", ())
    assert any(
        tuple(item["columns"].split(",")) == ("tenant_id", "id")
        and (item.get("kind") == "u" or item.get("constraint_type", "").upper() == "UNIQUE")
        for item in constraints
    ), constraints
