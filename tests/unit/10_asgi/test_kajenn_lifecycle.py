# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Server factories defer persistence and honour deployment recipes at startup."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kajenn_mail_proxy import mail_proxy_application as application_module
from kajenn_mail_proxy import server as server_module


def fake_core():
    return SimpleNamespace(start=AsyncMock(), stop=AsyncMock())


def test_import_and_factory_do_not_create_engine_or_database(monkeypatch, tmp_path):
    engine = Mock(side_effect=AssertionError("engine constructed before startup"))
    database = Mock(side_effect=AssertionError("database opened before startup"))
    monkeypatch.setattr(application_module, "MailProxy", engine)
    monkeypatch.setattr(application_module, "MailProxyDb", database)
    module = importlib.reload(server_module)
    path = tmp_path / "unopened.db"
    server = module.build_server(db_path=str(path))
    assert server.applications["mailproxy"]._core is None
    assert not hasattr(module, "app")
    assert not path.exists()
    engine.assert_not_called()
    database.assert_not_called()


@pytest.mark.asyncio
async def test_injected_core_is_started_and_stopped_without_replacement(monkeypatch):
    core = fake_core()
    constructor = Mock(side_effect=AssertionError("injected engine replaced"))
    monkeypatch.setattr(application_module, "MailProxy", constructor)
    server = server_module.build_server(core=core, api_token="admin")
    app = server.applications["mailproxy"]
    monkeypatch.setattr(app, "_configure_bounce", AsyncMock())
    assert app.core is core
    core.start.assert_not_awaited()
    await app.on_startup()
    core.start.assert_awaited_once()
    await app.on_shutdown()
    core.stop.assert_awaited_once()
    constructor.assert_not_called()


@pytest.mark.asyncio
async def test_recipe_overrides_environment_and_creates_engine_at_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("GMP_MAX_CONCURRENT_SENDS", "17")
    monkeypatch.setenv("GMP_CACHE_MEMORY_MAX_MB", "23")
    core = fake_core()
    constructor = Mock(return_value=core)
    persistence = object()
    database = Mock(return_value=persistence)
    monkeypatch.setattr(application_module, "MailProxy", constructor)
    monkeypatch.setattr(application_module, "MailProxyDb", database)
    path = tmp_path / "configured.db"
    server = server_module.build_server(
        db_path=str(path), engine_options={"max_concurrent_sends": 5, "test_mode": True},
        cache_options={"memory_max_mb": 7}, api_token="recipe-token",
    )
    app = server.applications["mailproxy"]
    monkeypatch.setattr(app, "_configure_bounce", AsyncMock())
    constructor.assert_not_called()
    assert app.api_token == "recipe-token"
    await app.on_startup()
    database.assert_called_once_with(str(path))
    kwargs = constructor.call_args.kwargs
    assert kwargs["persistence"] is persistence
    assert kwargs["max_concurrent_sends"] == 5
    assert kwargs["test_mode"] is True
    assert kwargs["cache_config"].memory_max_mb == 7
    await app.on_shutdown()
    core.stop.assert_awaited_once()
