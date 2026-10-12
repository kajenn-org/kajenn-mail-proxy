"""Behavioral contract for Asqueel persistence and Kajenn worker ownership."""

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import pytest
from asqueel import AsqueelDb

from kajenn_mail_proxy.asqueel_db import MailProxyDb

pytestmark = pytest.mark.asyncio


class RecordingRunner:
    """Stand-in for server.run_sync; production owns its executor."""

    def __init__(self):
        self.threads = []

    async def __call__(self, fn, *args, **kwargs):
        def invoke():
            self.threads.append(threading.get_ident())
            return fn(*args, **kwargs)

        return await asyncio.to_thread(invoke)


async def test_runtime_operations_complete_on_the_same_worker(tmp_path, monkeypatch):
    runner = RecordingRunner()
    db = MailProxyDb(str(tmp_path / "worker.db"), run_sync=runner)
    await db.init_db()
    assert isinstance(db.runtime, AsqueelDb)
    loop_thread = threading.get_ident()
    events = []
    for name in ("execute", "commit", "closeConnection"):
        original = getattr(db.runtime, name)

        def record(*args, _name=name, _original=original, **kwargs):
            events.append((_name, threading.get_ident()))
            return _original(*args, **kwargs)

        monkeypatch.setattr(db.runtime, name, record)
    try:
        await db.add_tenant({"id": "worker", "name": "Worker"})
        assert events
        assert {thread for _, thread in events} == {runner.threads[-1]}
        assert runner.threads[-1] != loop_thread
        assert "commit" in [name for name, _ in events]
        assert events[-1][0] == "closeConnection"
        events.clear()
        assert (await db.get_tenant("worker"))["name"] == "Worker"
        assert events
        assert {thread for _, thread in events} == {runner.threads[-1]}
        assert events[-1][0] == "closeConnection"
    finally:
        await db.close()


async def test_transaction_rolls_back_all_writes(tmp_path):
    db = MailProxyDb(str(tmp_path / "rollback.db"), run_sync=RecordingRunner())
    await db.init_db()

    def fail_after_write(store):
        store.add_tenant({"id": "rollback", "name": "Must disappear"})
        raise ValueError("failure after insert")

    try:
        with pytest.raises(ValueError, match="failure after insert"):
            await db.transaction(fail_after_write)
        assert await db.get_tenant("rollback") is None
        await db.add_tenant({"id": "after", "name": "Still usable"})
        assert await db.get_tenant("after") is not None
    finally:
        await db.close()


async def test_insert_messages_rolls_back_batch_on_second_entry_failure(tmp_path):
    db = MailProxyDb(str(tmp_path / "batch.db"), run_sync=RecordingRunner())
    await db.init_db()
    await db.add_tenant({"id": "tenant", "name": "Tenant"})
    try:
        with pytest.raises((TypeError, ValueError)):
            await db.insert_messages([
                {"id": "first", "tenant_id": "tenant", "payload": {"body": "valid"}},
                {"id": "second", "tenant_id": "tenant", "payload": {"body": object()}},
            ])
        assert await db.get_message("first", "tenant") is None
        assert await db.list_messages("tenant") == []
    finally:
        await db.close()


async def test_worker_does_not_block_event_loop(tmp_path):
    db = MailProxyDb(str(tmp_path / "responsive.db"), run_sync=RecordingRunner())
    await db.init_db()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    def blocking_operation(store):
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(3):
            raise TimeoutError("event loop did not release worker")
        return threading.get_ident()

    task = asyncio.create_task(db.transaction(blocking_operation))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # This line can run only if the synchronous callback is off the loop.
        release.set()
        assert await asyncio.wait_for(task, 2) != threading.get_ident()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await db.close()


async def test_reads_release_sqlite_transaction_locks(tmp_path):
    path = tmp_path / "locks.db"
    db = MailProxyDb(str(path), run_sync=RecordingRunner())
    await db.init_db()
    await db.add_tenant({"id": "tenant", "name": "Tenant"})
    try:
        await db.get_tenant("tenant")
        await db.list_accounts("tenant")
        await db.list_messages("tenant")

        def independent_writer():
            with sqlite3.connect(path, timeout=0.1) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("UPDATE tenants SET name = ? WHERE id = ?", ("External", "tenant"))

        await asyncio.to_thread(independent_writer)
        assert (await db.get_tenant("tenant"))["name"] == "External"
    finally:
        await db.close()


async def test_close_waits_for_worker_after_caller_cancellation(tmp_path):
    db = MailProxyDb(str(tmp_path / "cancel.db"), run_sync=RecordingRunner())
    await db.init_db()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()

    def operation(store):
        loop.call_soon_threadsafe(entered.set)
        try:
            if not release.wait(3):
                raise TimeoutError("worker was not released")
            store.add_tenant({"id": "completed", "name": "Completed"})
        finally:
            finished.set()

    task = asyncio.create_task(db.transaction(operation))
    close_task = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        close_task = asyncio.create_task(db.close())
        # Give cancellation and close several scheduling opportunities.
        for _ in range(5):
            await asyncio.sleep(0)
        assert not finished.is_set()
        assert not close_task.done()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(close_task, 2)
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if close_task is not None:
            await close_task
        else:
            await db.close()


async def test_legacy_main_file_and_composite_account_keys_are_preserved(tmp_path):
    path = tmp_path / "legacy.db"
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "legacy_077.sql"
    token_a = "legacy-077-test-token-a"
    hash_a = hashlib.sha256(token_a.encode()).hexdigest()
    hash_b = hashlib.sha256(b"legacy-077-test-token-b").hexdigest()
    payload = {"body": "Pending legacy message", "to": "test@example.com"}
    with sqlite3.connect(path) as connection:
        connection.executescript(fixture.read_text())
        connection.executemany(
            "INSERT INTO tenants (id,name,api_key_hash) VALUES (?,?,?)",
            [("a", "Legacy A", hash_a), ("b", "Legacy B", hash_b)],
        )
        connection.executemany(
            "INSERT INTO accounts (id,tenant_id,host,port) VALUES (?,?,?,?)",
            [("shared", "a", "smtp.a.example", 587), ("shared", "b", "smtp.b.example", 465)],
        )
        connection.execute(
            "INSERT INTO messages (pk,id,tenant_id,account_id,payload) VALUES (?,?,?,?,?)",
            ("legacy-message-pk", "legacy-message", "a", "shared", json.dumps(payload)),
        )
        connection.execute(
            "INSERT INTO message_events (id,message_pk,event_type,event_ts,description,metadata) VALUES (?,?,?,?,?,?)",
            (17, "legacy-message-pk", "deferred", 100, "Legacy event", '{"reason":"test"}'),
        )
    db = MailProxyDb(str(path), run_sync=RecordingRunner())
    try:
        await db.init_db()
        assert (await db.get_tenant("a"))["api_key_hash"] == hash_a
        assert (await db.tenants.get_tenant_by_token(token_a))["id"] == "a"
        message = await db.get_message("legacy-message", "a")
        assert message["pk"] == "legacy-message-pk"
        assert message["message"] == payload
        assert message["smtp_ts"] is None
        events = await db.get_events_for_message("legacy-message-pk")
        assert len(events) == 1
        assert events[0]["event_id"] == 17
        assert events[0]["description"] == "Legacy event"
        assert (await db.get_account("a", "shared"))["host"] == "smtp.a.example"
        assert (await db.get_account("b", "shared"))["host"] == "smtp.b.example"
        await db.add_account({"id": "shared", "tenant_id": "a", "host": "updated.example", "port": 25})
        assert (await db.get_account("b", "shared"))["host"] == "smtp.b.example"
    finally:
        await db.close()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT host FROM accounts WHERE tenant_id='a' AND id='shared'").fetchone() == ("updated.example",)
        assert connection.execute("SELECT api_key_hash FROM tenants WHERE id='a'").fetchone() == (hash_a,)
        assert connection.execute("SELECT payload,smtp_ts FROM messages WHERE pk='legacy-message-pk'").fetchone() == (json.dumps(payload), None)
        assert connection.execute("SELECT id,description FROM message_events").fetchall() == [(17, "Legacy event")]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO accounts (id,tenant_id,host,port) VALUES ('shared','b','duplicate',25)")
    assert not list(tmp_path.glob("legacy_*.db"))


async def test_unsupported_partial_schema_upgrade_fails_atomically(tmp_path):
    path = tmp_path / "unsupported.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT); INSERT INTO tenants VALUES ('legacy', 'Keep me');")
        before_schema = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    db = MailProxyDb(str(path), run_sync=RecordingRunner())
    try:
        with pytest.raises(Exception, match=r"(?i)(schema|migrat|created_at|default)"):
            await db.init_db()
    finally:
        await db.close()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == before_schema
        assert connection.execute("SELECT * FROM tenants").fetchall() == [("legacy", "Keep me")]


@pytest.mark.parametrize("delete", ["account", "tenant"])
async def test_delete_account_preserves_other_tenant_messages_and_events(tmp_path, delete):
    db = MailProxyDb(str(tmp_path / "delete-isolation.db"), run_sync=RecordingRunner())
    await db.init_db()
    try:
        for tenant in ("a", "b"):
            await db.add_tenant({"id": tenant, "name": tenant})
            await db.add_account({"id": "shared", "tenant_id": tenant, "host": f"smtp.{tenant}.example", "port": 587})
        inserted = await db.insert_messages([
            {"id": "same-message", "tenant_id": "a", "account_id": "shared", "payload": {"body": "A"}},
            {"id": "same-message", "tenant_id": "b", "account_id": "shared", "payload": {"body": "B"}},
        ])
        for message in inserted:
            await db.add_event(message["pk"], "deferred", 123, "Keep tenant boundary")
        tenant_a = await db.get_message("same-message", "a")
        tenant_b = await db.get_message("same-message", "b")
        await db.log_send("shared", 123)
        if delete == "account":
            await db.delete_account("a", "shared")
        else:
            await db.delete_tenant("a")
        assert await db.get_events_for_message(tenant_a["pk"]) == []
        assert await db.count_sends_since("shared", 0) == 1
        assert await db.list_accounts("a") == []
        assert (await db.get_account("b", "shared"))["host"] == "smtp.b.example"
        assert await db.get_message("same-message", "a") is None
        assert (await db.get_message("same-message", "b"))["pk"] == tenant_b["pk"]
        remaining_events = await db.get_events_for_message(tenant_b["pk"])
        assert len(remaining_events) == 1
        assert remaining_events[0]["description"] == "Keep tenant boundary"
        await db.delete_account("b", "shared")
        assert await db.count_sends_since("shared", 0) == 0
        assert await db.get_events_for_message(tenant_b["pk"]) == []
    finally:
        await db.close()


async def test_tenant_environment_is_reset_after_error_on_reused_worker(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    # One worker makes thread reuse deterministic; only the test owns this pool.
    with ThreadPoolExecutor(max_workers=1) as executor:
        async def run_sync(fn, *args, **kwargs):
            return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))

        db = MailProxyDb(str(tmp_path / "environment.db"), run_sync=run_sync)
        await db.init_db()
        observed = []

        def fail(store):
            observed.append((threading.get_ident(), db.runtime.currentEnv.get("tenant_id")))
            db.runtime.updateEnv(leaked_marker="must disappear")
            raise ValueError("tenant operation failed")

        def inspect_environment(store):
            return threading.get_ident(), dict(db.runtime.currentEnv)

        try:
            with pytest.raises(ValueError, match="tenant operation failed"):
                await db.transaction(fail, tenant_id="tenant-a")
            thread, environment = await db.transaction(inspect_environment, tenant_id="tenant-b")
            assert observed == [(thread, "tenant-a")]
            assert environment.get("tenant_id") == "tenant-b"
            assert "leaked_marker" not in environment
            same_thread, neutral = await db.transaction(inspect_environment)
            assert same_thread == thread
            assert neutral.get("tenant_id") is None
            assert "leaked_marker" not in neutral
        finally:
            await db.close()


async def test_cancelled_close_still_finishes_cleanup_after_worker(tmp_path):
    db = MailProxyDb(":memory:", run_sync=RecordingRunner())
    await db.init_db()
    temporary = Path(db._store.location)
    assert temporary.exists()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    def operation(store):
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(3):
            raise TimeoutError("worker was not released")
        store.add_tenant({"id": "last", "name": "Last write"})

    work = asyncio.create_task(db.transaction(operation))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        closing = asyncio.create_task(db.close())
        for _ in range(5):
            await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert not work.done()
        assert temporary.exists()
        release.set()
        await asyncio.wait_for(work, 2)

        async def cleanup_complete():
            while not db._closed:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(cleanup_complete(), 2)
        assert not temporary.exists()
        await db.close()
        assert db._closed
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await db.close()
