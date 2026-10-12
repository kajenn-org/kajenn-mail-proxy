"""Async facade: each complete synchronous operation runs in a Kajenn worker."""

from __future__ import annotations

import asyncio
import copy
import inspect
from functools import partial


class AsyncAccess:
    def __init__(self, owner, target):
        self._owner = owner
        self._target = target

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        value = getattr(self._target, name)
        # Pure helpers retain the synchronous public contract; no database I/O.
        if name in {"is_batch_suspended", "create_table_sql", "pk_column"}:
            return value
        if callable(value):

            async def invoke(*args, **kwargs):
                args, kwargs = copy.deepcopy((args, kwargs))
                bound = inspect.signature(value).bind_partial(*args, **kwargs).arguments
                tenant_id = bound.get("tenant_id")
                if tenant_id is None:
                    tenant = bound.get("tenant")
                    account = bound.get("acc")
                    if isinstance(tenant, dict):
                        tenant_id = tenant.get("id")
                    elif isinstance(account, dict):
                        tenant_id = account.get("tenant_id")
                return await self._owner._invoke(
                    partial(value, *args, **kwargs), tenant_id=tenant_id
                )

            return invoke
        return value


class AsyncStore:
    def __init__(self, store, *, run_sync=None, runtime=None):
        self._store = store
        if runtime is not None:
            store._runtime = runtime
        self.run_sync = run_sync or asyncio.to_thread
        self._pending = set()
        self._closing = False
        self._closed = False
        self._close_task = None
        self.db_path = store.connection_string
        self.connection_string = store.connection_string
        self._access = AsyncAccess(self, store)

    @property
    def runtime(self):
        return self._store.runtime

    @property
    def tables(self):
        return {name: AsyncAccess(self, table) for name, table in self._store.tables.items()}

    @property
    def adapter(self):
        return AsyncAccess(self, self._store.adapter)

    def table(self, name):
        return AsyncAccess(self, self._store.table(name))

    def add_table(self, table_class):
        return AsyncAccess(self, self._store.add_table(table_class))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        value = getattr(self._store, name)
        if any(value is t for t in self._store.tables.values()):
            return AsyncAccess(self, value)
        return getattr(self._access, name)

    async def connect(self):
        if self._closed:
            raise RuntimeError("Persistence is closed")

    async def transaction(self, operation, *, tenant_id=None):
        """Execute a trusted synchronous callback atomically against the domain store."""
        return await self._invoke(partial(operation, self._store), tenant_id=tenant_id)

    def _work(self, operation, tenant_id):
        db = self.runtime
        db.acquire()
        try:
            with db.tempEnv(tenant_id=tenant_id):
                if self._store.backend == "postgresql":
                    db.execute("SET LOCAL search_path TO public")
                result = operation()
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise TypeError("Database units of work must be synchronous")
                db.commit()
                return result
        except BaseException as error:
            try:
                db.rollback()
            except BaseException as rollback_error:
                error.add_note(f"Rollback failed: {rollback_error}")
            raise
        finally:
            db.closeConnection()

    async def _invoke(self, operation, *, tenant_id=None):
        if self._closing or self._closed:
            raise RuntimeError("Persistence is closing")
        task = asyncio.create_task(self.run_sync(self._work, operation, tenant_id))
        self._pending.add(task)

        def completed(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _drain(self):
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        self._store.dispose()
        self._closed = True

    async def close(self):
        """Drain workers even if the caller stops waiting for shutdown."""
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._drain())
        await asyncio.shield(self._close_task)
