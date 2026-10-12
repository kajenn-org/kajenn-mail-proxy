# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Synchronous SQL helpers executed exclusively through the Asqueel runtime.

Identifiers and optional SQL fragments come from trusted application schemas;
all record values use Asqueel's portable named parameter binding.
"""

from __future__ import annotations

from collections.abc import Sequence
from sqlite3 import complete_statement
from typing import Any


class SqlOperations:
    """Legacy CRUD signatures with synchronous Asqueel execution."""

    placeholder = ":name"

    def __init__(self, store):
        self.store = store

    @property
    def runtime(self):
        return self.store.runtime

    def pk_column(self, name: str) -> str:
        quoted = '"' + name.replace('"', '""') + '"'
        kind = "SERIAL" if self.store.backend == "postgresql" else "INTEGER"
        return f"{quoted} {kind} PRIMARY KEY"

    def _sql_name(self, name: str) -> str:
        return '"user"' if name.lower() == "user" else name

    def _placeholder(self, name: str) -> str:
        return ":" + name

    def execute(self, query: str, params: dict[str, Any] | None = None) -> int:
        return self.runtime.execute(query, params).rowcount

    def execute_many(self, query: str, params_list: Sequence[dict[str, Any]]) -> int:
        return sum(self.execute(query, params) for params in params_list)

    def fetch_all(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [dict(row) for row in self.runtime.execute(query, params).rows]

    def fetch_one(self, query: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.fetch_all(query, params)
        return rows[0] if rows else None

    def execute_script(self, script: str) -> None:
        """Split trusted schema SQL without breaking quoted semicolons or triggers."""
        statement = ""
        for char in script:
            statement += char
            if char == ";" and complete_statement(statement):
                self.execute(statement)
                statement = ""
        if statement.strip():
            # A final statement does not need a terminating semicolon. A tail
            # containing only comments is accepted by the runtime as trusted SQL.
            self.execute(statement)

    def column_names(self, table: str) -> list[str]:
        if self.store.backend == "sqlite":
            quoted = '"' + table.replace('"', '""') + '"'
            rows = self.fetch_all(f"PRAGMA table_info({quoted})")
            return [row["name"] for row in rows]
        rows = self.fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table "
            "ORDER BY ordinal_position",
            {"schema": self.store.schema, "table": table},
        )
        return [row["column_name"] for row in rows]

    def commit(self) -> None:
        self.runtime.commit()

    def rollback(self) -> None:
        self.runtime.rollback()

    def insert(self, table: str, values: dict[str, Any]) -> int:
        columns = list(values)
        names = ", ".join(self._sql_name(c) for c in columns)
        bindings = ", ".join(self._placeholder(c) for c in columns)
        return self.execute(
            f"INSERT INTO {self._sql_name(table)} ({names}) VALUES ({bindings})", values
        )

    def select(
        self,
        table: str,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        names = ", ".join(self._sql_name(c) for c in columns) if columns else "*"
        query = f"SELECT {names} FROM {self._sql_name(table)}"
        if where:
            query += " WHERE " + " AND ".join(
                f"{self._sql_name(k)} = {self._placeholder(k)}" for k in where
            )
        if order_by:
            query += f" ORDER BY {order_by}"
        params = dict(where or {})
        if limit is not None:
            binding = "_limit"
            while binding in params:
                binding = "_" + binding
            query += f" LIMIT :{binding}"
            params[binding] = limit
        return self.fetch_all(query, params)

    def select_one(
        self,
        table: str,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        rows = self.select(table, columns, where, limit=1)
        return rows[0] if rows else None

    def update(self, table: str, values: dict[str, Any], where: dict[str, Any]) -> int:
        assignments = [f"{self._sql_name(k)} = {self._placeholder('val_' + k)}" for k in values]
        conditions = [f"{self._sql_name(k)} = {self._placeholder('whr_' + k)}" for k in where]
        params = {f"val_{k}": v for k, v in values.items()}
        params.update({f"whr_{k}": v for k, v in where.items()})
        return self.execute(
            f"UPDATE {self._sql_name(table)} SET {', '.join(assignments)} WHERE {' AND '.join(conditions)}",
            params,
        )

    def delete(self, table: str, where: dict[str, Any]) -> int:
        conditions = [f"{self._sql_name(k)} = {self._placeholder(k)}" for k in where]
        return self.execute(
            f"DELETE FROM {self._sql_name(table)} WHERE {' AND '.join(conditions)}",
            where,
        )

    def exists(self, table: str, where: dict[str, Any]) -> bool:
        return self.select_one(table, columns=["1"], where=where) is not None

    def upsert(
        self,
        table: str,
        data: dict[str, Any],
        conflict_columns: Sequence[str],
        update_extras: Sequence[str] | None = None,
    ) -> int:
        columns = list(data)
        names = ", ".join(self._sql_name(c) for c in columns)
        bindings = ", ".join(self._placeholder(c) for c in columns)
        conflicts = ", ".join(self._sql_name(c) for c in conflict_columns)
        assignments = [
            f"{self._sql_name(c)} = EXCLUDED.{self._sql_name(c)}"
            for c in columns
            if c not in conflict_columns
        ]
        assignments.extend(update_extras or ())
        action = "DO UPDATE SET " + ", ".join(assignments) if assignments else "DO NOTHING"
        return self.execute(
            f"INSERT INTO {self._sql_name(table)} ({names}) VALUES ({bindings}) ON CONFLICT ({conflicts}) {action}",
            data,
        )

    def count(self, table: str, where: dict[str, Any] | None = None) -> int:
        query = f"SELECT COUNT(*) AS cnt FROM {self._sql_name(table)}"
        if where:
            query += " WHERE " + " AND ".join(
                f"{self._sql_name(k)} = {self._placeholder(k)}" for k in where
            )
        row = self.fetch_one(query, where or {})
        return row["cnt"] if row else 0
