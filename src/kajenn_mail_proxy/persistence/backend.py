"""Synchronous mail store using Asqueel's SQL execution runtime."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from asqueel import AsqueelDb, SqlDatabaseConfig

from .operations import SqlOperations


class SyncSqlStore:
    def __init__(self, connection_string):
        connection_string = str(connection_string)
        is_path = (
            connection_string.startswith(("/", "./", "../")) or connection_string == ":memory:"
        )
        if not is_path:
            if ":" not in connection_string:
                raise ValueError(
                    "Invalid connection string: use an explicit SQLite path or database scheme"
                )
            scheme = connection_string.split(":", 1)[0]
            if scheme not in {"sqlite", "postgresql", "postgres"}:
                raise ValueError(f"Unknown database type: {scheme}. Supported: sqlite, postgresql")
        self.connection_string = connection_string
        self.backend = (
            "postgresql" if connection_string.startswith(("postgresql:", "postgres:")) else "sqlite"
        )
        self.schema = "public" if self.backend == "postgresql" else "main"
        self.location = (
            connection_string.replace("postgres://", "postgresql://", 1)
            if self.backend == "postgresql"
            else connection_string.removeprefix("sqlite:")
        )
        self._temporary = None
        if self.backend == "sqlite" and self.location == ":memory:":
            fd, self._temporary = tempfile.mkstemp(prefix="kajenn-mail-", suffix=".db")
            os.close(fd)
            self.location = self._temporary
        self.tables = {}
        self.adapter = SqlOperations(self)
        self._runtime = None
        self._runtime_lock = threading.Lock()

    @property
    def runtime(self):
        with self._runtime_lock:
            if self._runtime is None:
                self._runtime = AsqueelDb(self.recipe())
            return self._runtime

    def recipe(self):
        store = self

        class MailDatabaseConfig(SqlDatabaseConfig):
            def main(self, root):
                db = root.db(implementation=store.backend, conninfo=store.location)
                tables = db.schemas().schema(store.schema).tables()
                for table in store.tables.values():
                    keys = [col.name for col in table.columns.values() if col.primary_key]
                    if table.name == "accounts":
                        keys = ["tenant_id", "id"]
                    options = {"pkey": ",".join(keys)} if keys else {}
                    model_table = tables.table(table.name, **options)
                    if table.name == "messages":
                        model_table.constraints().constraint(
                            "messages_tenant_id_id_key",
                            constraint_type="UNIQUE",
                            columns="tenant_id,id",
                        )
                    columns = model_table.columns()
                    for col in table.columns.values():
                        dtype = {
                            "INTEGER": "I",
                            "TEXT": "T",
                            "REAL": "R",
                            "TIMESTAMP": "DH",
                            "BLOB": "O",
                        }[col.type_]
                        attrs = {"dtype": dtype, "notnull": not col.nullable, "unique": col.unique}
                        if col.default is not None:
                            attrs["sqldefault"] = (
                                str(col.default)
                                if not isinstance(col.default, str)
                                or col.default == "CURRENT_TIMESTAMP"
                                else "'" + col.default.replace("'", "''") + "'"
                            )
                        node = columns.column(col.name, **attrs)
                        if col.relation_sql:
                            node.relation(
                                f"{store.schema}.{col.relation_table}.{col.relation_pk}",
                                foreign_key=True,
                                indexed=False,
                            )

        return MailDatabaseConfig

    def add_table(self, table_class):
        if self._runtime is not None:
            raise RuntimeError("Register tables before the first database operation")
        table = table_class(self)
        self.tables[table.name] = table
        return table

    def table(self, name):
        return self.tables[name]

    def connect(self):
        pass

    def check_structure(self):
        for table in self.tables.values():
            table.create_schema()

    def execute(self, query, params=None):
        return self.adapter.execute(query, params)

    def fetch_one(self, query, params=None):
        return self.adapter.fetch_one(query, params)

    def fetch_all(self, query, params=None):
        return self.adapter.fetch_all(query, params)

    def commit(self):
        self.runtime.commit()

    def rollback(self):
        self.runtime.rollback()

    def dispose(self):
        if self._temporary:
            Path(self._temporary).unlink(missing_ok=True)
            self._temporary = None
