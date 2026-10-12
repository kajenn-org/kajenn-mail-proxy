# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Real Kajenn lifecycle and worker-pool contracts for Asqueel persistence."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
import pytest_asyncio
from asqueel import AsqueelDb
from kajenn.db import AsgiDbHandlerBase

from kajenn_mail_proxy.server import build_server

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def running_server(tmp_path):
    server = build_server(
        db_path=str(tmp_path / "lifecycle.db"),
        api_token="lifecycle-admin",
        engine_options={"start_active": False, "test_mode": True},
        bounce_options={"enabled": False},
    )
    await server.lifespan.startup()
    context = SimpleNamespace(server=server, app=server.applications["mailproxy"], stopped=False)
    try:
        yield context
    finally:
        if not context.stopped:
            await server.lifespan.shutdown()


def registered_store(context):
    server, app = context.server, context.app
    assert "mail" in server.databases, "Mail persistence must be registered in Kajenn"
    handler = server.databases["mail"]
    assert isinstance(handler, AsgiDbHandlerBase)
    assert isinstance(app.core.db.runtime, AsqueelDb)
    assert handler._db is app.core.db.runtime
    assert app.core.db.run_sync == server.run_sync
    return app.core.db


async def wait_started(started):
    async with asyncio.timeout(5):
        while not started.is_set():
            await asyncio.sleep(0.01)


async def test_runtime_is_registered_and_uses_the_servers_worker_pool(running_server):
    store = registered_store(running_server)
    loop_thread = threading.get_ident()

    def read_identity(sync_store):
        return threading.get_ident(), sync_store.adapter.fetch_one("SELECT 1 AS value")

    worker_thread, row = await store.transaction(read_identity)
    assert worker_thread != loop_thread
    assert row == {"value": 1}


async def test_slow_transaction_keeps_event_loop_responsive(running_server):
    store = registered_store(running_server)
    started, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def slow_read(sync_store):
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("Test did not release the database worker")
        return threading.get_ident(), sync_store.adapter.fetch_one("SELECT 7 AS value")

    operation = asyncio.create_task(store.transaction(slow_read, tenant_id="tenant-a"))
    try:
        await wait_started(started)
        ticks = 0
        for _ in range(5):
            await asyncio.sleep(0.01)
            ticks += 1
        assert ticks == 5
        assert not operation.done(), "Worker must still be blocked while the loop progresses"
        assert store._pending
    finally:
        release.set()
        result = await operation
    assert result[0] != loop_thread
    assert result[1] == {"value": 7}
    await asyncio.sleep(0)
    assert not store._pending


async def test_shutdown_drains_in_flight_transaction_before_closing(running_server):
    store = registered_store(running_server)
    started, release = threading.Event(), threading.Event()

    def slow_write(sync_store):
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("Test did not release the database worker")
        sync_store.adapter.execute("CREATE TABLE lifecycle_marker (value INTEGER)")
        sync_store.adapter.execute(
            "INSERT INTO lifecycle_marker(value) VALUES (:value)", {"value": 19}
        )
        return 19

    operation = asyncio.create_task(store.transaction(slow_write))
    shutdown = None
    try:
        await wait_started(started)
        assert store._pending
        shutdown = asyncio.create_task(running_server.server.lifespan.shutdown())
        await asyncio.sleep(0.05)
        assert not shutdown.done(), "Shutdown must await the in-flight database worker"
    finally:
        release.set()
        result = await operation
        if shutdown is not None:
            await shutdown
            running_server.stopped = True
    assert result == 19
    assert not store._pending
    assert store._closed
    with pytest.raises(RuntimeError, match="closing|closed"):
        await store.transaction(lambda sync_store: sync_store.adapter.fetch_one("SELECT 1"))
