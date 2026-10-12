"""Real PostgreSQL parity tests for the Asqueel backend."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import time

import pytest
import pytest_asyncio

psycopg = pytest.importorskip("psycopg")

from kajenn_mail_proxy.asqueel_db import MailProxyDb

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def asqueel_pg_dsn():
    """Publish only a random localhost port; always remove the disposable server."""
    container = subprocess.check_output([
        "docker", "run", "--detach", "--rm", "--publish", "127.0.0.1::5432",
        "--env", "POSTGRES_PASSWORD=test-only-password", "--env", "POSTGRES_DB=mailtest",
        "postgres:17",
    ], text=True).strip()
    try:
        port = subprocess.check_output(["docker", "port", container, "5432/tcp"], text=True).strip().rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:test-only-password@127.0.0.1:{port}/mailtest"
        deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(dsn, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    logs = subprocess.check_output(["docker", "logs", container], text=True, stderr=subprocess.STDOUT)
                    pytest.fail(f"PostgreSQL did not become ready: {logs}")
                time.sleep(0.1)
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "--force", container], check=True, capture_output=True)


@pytest.fixture
def clean_pg_dsn(asqueel_pg_dsn):
    with psycopg.connect(asqueel_pg_dsn, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    return asqueel_pg_dsn


@pytest_asyncio.fixture
async def asqueel_pg_db(clean_pg_dsn):
    db = MailProxyDb(clean_pg_dsn, run_sync=asyncio.to_thread)
    try:
        await db.init_db()
        yield db
    finally:
        await db.close()


async def test_postgresql_queue_accounts_events_and_reporting(asqueel_pg_db):
    db = asqueel_pg_db
    for tenant in ("a", "b"):
        token = await db.add_tenant({"id": tenant, "name": tenant})
        assert (await db.tenants.get_tenant_by_token(token))["id"] == tenant
        await db.add_account({"tenant_id": tenant, "id": "shared", "host": f"smtp.{tenant}.example", "port": 587})
    assert (await db.get_account("a", "shared"))["host"] == "smtp.a.example"
    assert (await db.get_account("b", "shared"))["host"] == "smtp.b.example"
    inserted = await db.insert_messages([
        {"id": "same", "tenant_id": tenant, "account_id": "shared", "payload": {"body": tenant}}
        for tenant in ("a", "b")
    ])
    assert len({row["pk"] for row in inserted}) == 2
    a = await db.get_message("same", "a")
    assert a["message"] == {"body": "a"}
    assert len(await db.list_messages("a")) == 1
    assert await db.count_pending_messages("a") == 1
    assert len(await db.fetch_ready_messages(limit=10, now_ts=int(time.time()))) == 2
    event_id = await db.add_event(a["pk"], "deferred", 123, "PG returning", {"probe": True})
    assert isinstance(event_id, int) and event_id > 0
    events = await db.fetch_unreported_events(10)
    assert [event["event_id"] for event in events] == [event_id]
    await db.mark_events_reported([event_id], 456)
    assert await db.fetch_unreported_events(10) == []
    await db.mark_sent(a["pk"], 789)
    assert await db.count_pending_messages("a") == 0
    assert await db.count_pending_messages("b") == 1
    command_id = await db.log_command("/messages", {"id": "same"}, tenant_id="a", response_status=200, response_body={"ok": True})
    assert isinstance(command_id, int) and command_id > 0
    commands = await db.list_commands(tenant_id="a")
    assert len(commands) == 1
    assert commands[0]["id"] == command_id
    assert commands[0]["payload"] == {"id": "same"}


async def test_postgresql_rollback_and_reuse(asqueel_pg_db):
    db = asqueel_pg_db
    def fail(store):
        store.add_tenant({"id": "rollback", "name": "Discard"})
        raise ValueError("rollback PostgreSQL")
    with pytest.raises(ValueError, match="rollback PostgreSQL"):
        await db.transaction(fail)
    assert await db.get_tenant("rollback") is None
    await db.add_tenant({"id": "tenant", "name": "Keep"})
    with pytest.raises((TypeError, ValueError)):
        await db.insert_messages([
            {"id": "first", "tenant_id": "tenant", "payload": {"body": "valid"}},
            {"id": "second", "tenant_id": "tenant", "payload": {"body": object()}},
        ])
    assert await db.get_message("first", "tenant") is None
    assert await db.count_pending_messages("tenant") == 0


async def test_postgresql_legacy_077_preserves_auth_and_queue(clean_pg_dsn):
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "legacy_077_postgresql.sql"
    token = "postgresql-legacy-test-token"
    digest = hashlib.sha256(token.encode()).hexdigest()
    payload = json.dumps({"body": "Original legacy PostgreSQL"})
    with psycopg.connect(clean_pg_dsn) as connection:
        connection.execute(fixture.read_text())
        connection.execute("INSERT INTO tenants (id,name,api_key_hash) VALUES (%s,%s,%s)", ("legacy", "Legacy", digest))
        connection.execute("INSERT INTO accounts (id,tenant_id,host,port) VALUES (%s,%s,%s,%s)", ("smtp", "legacy", "smtp.example.com", 587))
        connection.execute("INSERT INTO messages (pk,id,tenant_id,account_id,payload) VALUES (%s,%s,%s,%s,%s)", ("legacy-pk", "legacy-message", "legacy", "smtp", payload))
        connection.execute("INSERT INTO message_events (message_pk,event_type,event_ts) VALUES (%s,%s,%s)", ("legacy-pk", "deferred", 123))
    db = MailProxyDb(clean_pg_dsn, run_sync=asyncio.to_thread)
    try:
        await db.init_db()
        assert (await db.tenants.get_tenant_by_token(token))["id"] == "legacy"
        assert (await db.get_tenant("legacy"))["api_key_hash"] == digest
        assert (await db.get_message("legacy-message", "legacy"))["message"] == json.loads(payload)
        assert await db.count_pending_messages("legacy") == 1
        events = await db.get_events_for_message("legacy-pk")
        assert len(events) == 1 and events[0]["event_type"] == "deferred"
    finally:
        await db.close()
    with psycopg.connect(clean_pg_dsn) as connection:
        assert connection.execute("SELECT payload FROM public.messages WHERE pk='legacy-pk'").fetchone() == (payload,)


async def test_postgresql_named_binding_casts_literals_and_comments(asqueel_pg_db):
    row = await asqueel_pg_db.adapter.fetch_one(
        """SELECT :word::text AS word, :number::int AS number,
                  :document::jsonb AS document, ':literal'::text AS literal
           /* :ignored_block */ -- :ignored_line
        """,
        {"word": "bound", "number": "42", "document": '{"ok":true}'},
    )
    assert row == {"word": "bound", "number": 42, "document": {"ok": True}, "literal": ":literal"}
