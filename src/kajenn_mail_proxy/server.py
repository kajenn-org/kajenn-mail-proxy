# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Standalone Kajenn factory. Run uvicorn kajenn_mail_proxy.server:build_server --factory."""

from __future__ import annotations

import json
import os
from typing import Any

from kajenn import AsgiServer
from kajenn.config import AsgiConfigBuilder

from .configuration import BounceSettings, CacheSettings, EngineSettings
from .mail_proxy_application import MailProxyApplication


def _environment_settings(model, prefix):
    values = {}
    for name in model.model_fields:
        value = os.environ.get(prefix + name.upper())
        if value is not None:
            values[name] = json.loads(value) if name == "retry_delays" else value
    return model.model_validate(values).model_dump()


def build_server(
    *,
    core=None,
    persistence=None,
    api_token=None,
    db_path=None,
    app_class=MailProxyApplication,
    allow_anonymous_admin=False,
    engine_options=None,
    cache_options=None,
    bounce_options=None,
    **kwargs: Any,
) -> AsgiServer:
    """Construct a configured server; database I/O starts only in its lifespan.

    Explicit arguments override environment values. Existing DB admin tokens
    are not loaded implicitly: set GMP_API_TOKEN in the deployment.
    """
    engine = _environment_settings(EngineSettings, "GMP_")
    engine.update(engine_options or {})
    cache = _environment_settings(CacheSettings, "GMP_CACHE_")
    cache.update(cache_options or {})
    bounce = _environment_settings(BounceSettings, "GMP_BOUNCE_")
    bounce.update(bounce_options or {})
    token = api_token if api_token is not None else os.environ.get("GMP_API_TOKEN")
    location = db_path or os.environ.get("GMP_DB_PATH", "./mail_service.db")

    class Configuration(AsgiConfigBuilder):
        default_config = False

        def main(self, root):
            cfg = root.configuration()
            cfg.server().tasks(enabled=False)
            app = cfg.applications().application(
                code="mailproxy",
                mount="mailproxy",
                app_class=app_class,
                core=core,
                persistence=persistence,
                api_token=token,
                allow_anonymous_admin=allow_anonymous_admin,
            )
            app.database(path=location)
            app.engine(**EngineSettings.model_validate(engine).model_dump())
            app.cache(**CacheSettings.model_validate(cache).model_dump())
            app.bounce(**BounceSettings.model_validate(bounce).model_dump())
            app.request(error_codes="fastapi")

    return AsgiServer(Configuration, **kwargs)
