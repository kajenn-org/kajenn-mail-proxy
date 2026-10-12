# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SQL helper behavior against the real Asqueel SQLite runtime."""

from __future__ import annotations

import pytest

from kajenn_mail_proxy.persistence.backend import SyncSqlStore


@pytest.fixture
def sql(tmp_path):
    store = SyncSqlStore(str(tmp_path / "operations.db"))
    store.runtime.acquire()
    try:
        yield store.adapter
    finally:
        store.runtime.rollback()
        store.runtime.closeConnection()
        store.dispose()


def test_crud_quotes_reserved_user_and_binds_values(sql):
    sql.execute_script(
        'CREATE TABLE records (id INTEGER PRIMARY KEY, "user" TEXT, amount INTEGER);'
    )
    assert sql.column_names("records") == ["id", "user", "amount"]
    hostile = "O'Reilly; DROP TABLE records; --"
    assert sql.insert("records", {"id": 1, "user": hostile, "amount": 3}) == 1
    assert sql.exists("records", {"user": hostile})
    assert sql.select_one("records", ["user"], {"id": 1}) == {"user": hostile}
    assert sql.update("records", {"id": 2, "amount": 9}, {"id": 1}) == 1
    assert sql.select_one("records", where={"id": 1}) is None
    assert sql.count("records", {"amount": 9}) == 1
    assert sql.count("records", {"amount": 3}) == 0
    assert sql.delete("records", {"id": 2}) == 1
    assert not sql.exists("records", {"id": 2})
    assert sql.count("records") == 0


def test_execute_many_and_ordered_limited_select(sql):
    sql.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, label TEXT)")
    assert (
        sql.execute_many(
            "INSERT INTO entries (id, label) VALUES (:id, :label)",
            [{"id": 1, "label": "one"}, {"id": 2, "label": "two"}, {"id": 3, "label": "three"}],
        )
        == 3
    )
    assert sql.execute_many("INSERT INTO entries (id, label) VALUES (:id, :label)", []) == 0
    assert sql.select("entries", ["id"], order_by="id DESC", limit=2) == [{"id": 3}, {"id": 2}]
    assert sql.select("entries", limit=0) == []
    assert sql.fetch_one("SELECT label FROM entries WHERE id = :id", {"id": 2}) == {"label": "two"}
    assert sql.fetch_one("SELECT id FROM entries WHERE id = :id", {"id": 99}) is None


def test_upsert_updates_non_keys_and_extras_and_supports_key_only_rows(sql):
    sql.execute(
        'CREATE TABLE records (id INTEGER PRIMARY KEY, "user" TEXT, revision INTEGER DEFAULT 0)'
    )
    assert sql.upsert("records", {"id": 1, "user": "first"}, ["id"]) == 1
    assert (
        sql.upsert("records", {"id": 1, "user": "second"}, ["id"], ["revision = revision + 1"]) == 1
    )
    assert sql.select_one("records") == {"id": 1, "user": "second", "revision": 1}
    sql.execute("CREATE TABLE keys_only (id INTEGER PRIMARY KEY)")
    assert sql.upsert("keys_only", {"id": 1}, ["id"]) == 1
    assert sql.upsert("keys_only", {"id": 1}, ["id"]) == 0
    assert sql.count("keys_only") == 1


def test_script_preserves_quoted_semicolons_and_trigger_body(sql):
    sql.execute_script("""
        CREATE TABLE source (id INTEGER PRIMARY KEY, label TEXT);
        CREATE TABLE audit (label TEXT);
        CREATE TRIGGER record_insert AFTER INSERT ON source BEGIN
            INSERT INTO audit(label) VALUES ('first;second');
            INSERT INTO audit(label) VALUES (NEW.label);
        END;
        INSERT INTO source(id, label) VALUES (1, 'literal;value');
    """)
    assert sql.fetch_all("SELECT label FROM audit ORDER BY rowid") == [
        {"label": "first;second"},
        {"label": "literal;value"},
    ]


def test_named_binding_preserves_literal_and_comment_colons(sql):
    row = sql.fetch_one(
        "SELECT :value AS bound, ':not_a_parameter' AS literal -- :ignored\n", {"value": "kept"}
    )
    assert row == {"bound": "kept", "literal": ":not_a_parameter"}


def test_commit_and_rollback_control_transaction(sql):
    sql.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY)")
    sql.commit()
    sql.insert("entries", {"id": 1})
    sql.commit()
    sql.insert("entries", {"id": 2})
    sql.rollback()
    assert sql.select("entries", order_by="id") == [{"id": 1}]
