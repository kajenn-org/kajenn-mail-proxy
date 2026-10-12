# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""PostgreSQL integration fixtures using the Asqueel persistence facade."""
import asyncio
import os
import subprocess

import psycopg
import pytest
import pytest_asyncio

# Container context management guarantees cleanup; Ryuk is unnecessary here.
os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"


@pytest.fixture(scope="session")
def pg_container():
    from testcontainers.community.postgres import PostgresContainer

    if not os.environ.get("DOCKER_HOST"):
        endpoint = subprocess.check_output(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], text=True,
        ).strip()
        os.environ["DOCKER_HOST"] = endpoint
    with PostgresContainer("postgres:15", driver="psycopg") as postgres:
        yield postgres.get_connection_url().replace("postgresql+psycopg://", "postgresql://")


def reset_schema(dsn):
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")


@pytest_asyncio.fixture
async def pg_db(pg_container):
    from kajenn_mail_proxy.asqueel_db import MailProxyDb

    await asyncio.to_thread(reset_schema, pg_container)
    db = MailProxyDb(pg_container, run_sync=asyncio.to_thread)
    try:
        await db.init_db()
        yield db
    finally:
        await db.close()
        await asyncio.to_thread(reset_schema, pg_container)


@pytest_asyncio.fixture
async def pg_adapter(pg_db):
    """Raw CRUD uses the same complete worker units as domain operations."""
    yield pg_db.adapter
