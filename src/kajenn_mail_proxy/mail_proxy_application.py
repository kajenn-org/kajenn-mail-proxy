# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Mail application with a shared REST/MCP API and legacy REST v1 adapters."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from genro_routes import route
from kajenn import Avatar
from kajenn.applications.mcp import McpOpenApiApplication
from kajenn.exceptions import HTTPBadRequest, HTTPException, HTTPForbidden, HTTPUnauthorized
from kajenn.middleware.base import headers_dict
from kajenn.response import Response

from .asqueel_db import MailProxyDb
from .auth_tags import ADMIN_IDENTITY, ADMIN_TAG, API_TOKEN_HEADER, TENANT_TAG
from .config_loader import CacheConfig
from .configuration import CacheSettings, EngineSettings, MailProxyGrammar, read_settings
from .core import MailProxy
from .persistence.async_store import AsyncStore
from .routers import V1Routes
from .routers.api import MailApi

if TYPE_CHECKING:
    from genro_routes import RouterNode
    from kajenn.request import Request
    from kajenn.types import Receive, Scope, Send

    from .core import MailProxy

__all__ = ["ADMIN_TAG", "API_TOKEN_HEADER", "TENANT_TAG", "MailProxyApplication"]


class MailProxyApplication(McpOpenApiApplication):
    """kajenn application exposing the mail-proxy v1 REST contract."""

    code = "mailproxy"
    mount = "mailproxy"
    grammar = MailProxyGrammar
    mcp_name = "kajenn-mail-proxy"
    openapi_info = {"title": "Async Mail Service"}

    def __init__(self, **kwargs: Any) -> None:
        self._core: MailProxy | None = kwargs.pop("core", None)
        self._persistence = kwargs.pop("persistence", None)
        self._db_path = kwargs.pop("db_path", None)
        self._allow_anonymous_admin = kwargs.pop("allow_anonymous_admin", False)
        self._engine_options = kwargs.pop("engine_options", {})
        self._cache_options = kwargs.pop("cache_options", {})
        self._bounce_options = kwargs.pop("bounce_options", {})
        self._api_token: str | None = kwargs.pop("api_token", None)
        super().__init__(**kwargs)
        self.route.add_branches({"name": "v1", "instance": V1Routes(self)})
        self.route.add_branches({"name": "api", "cls": MailApi, "params": {"application": self}})

    @property
    def core(self) -> MailProxy:
        """The mail engine every handler delegates to."""
        if self._core is None:
            raise RuntimeError("Mail engine has not started")
        return self._core

    @property
    def api_token(self) -> str | None:
        """The configured global admin token; absence grants no privileges."""
        return self._api_token

    @property
    def validation_error_status(self) -> int:
        return 422

    async def on_startup(self) -> None:
        if self._core is None:
            settings = read_settings(self, "engine", EngineSettings, self._engine_options)
            cache = read_settings(self, "cache", CacheSettings, self._cache_options)
            db_path = self._db_path or self.config("database.path", default="./mail_service.db")
            persistence = self._persistence
            if persistence is None:
                handler = self.server.databases.get(self.db_name) if self.db_name else None
                runtime = handler.store.runtime if handler is not None else None
                persistence = MailProxyDb(db_path, run_sync=self.server.run_sync, runtime=runtime)
            self._core = MailProxy(
                persistence=persistence,
                cache_config=CacheConfig(**cache.model_dump()),
                **settings.model_dump(),
            )
        if isinstance(getattr(self.core, "db", None), AsyncStore):
            self.core.db.run_sync = self.server.run_sync
        try:
            await self.core.start()
            await self._configure_bounce()
        except BaseException:
            await self.core.stop()
            raise

    async def _configure_bounce(self) -> None:
        from .bounce import BounceConfig
        from .configuration import BounceSettings

        settings = read_settings(self, "bounce", BounceSettings, self._bounce_options)
        # Deployment configuration seeds only an unconfigured instance; runtime
        # admin updates persist on that row and take precedence thereafter.
        instance = await self.core.db.instance.ensure_instance()
        if settings.enabled and not instance.get("bounce_imap_host"):
            values = settings.model_dump()
            await self.core.db.instance.update_instance(
                {f"bounce_{key}": value for key, value in values.items()}
            )
        stored = await self.core.db.instance.get_bounce_config()
        if stored.get("enabled") and stored.get("imap_host"):
            self.core.configure_bounce_receiver(
                BounceConfig(
                    host=stored["imap_host"],
                    port=stored.get("imap_port") or 993,
                    user=stored.get("imap_user") or "",
                    password=stored.get("imap_password") or "",
                    use_ssl=stored.get("imap_ssl", True),
                    poll_interval=stored.get("poll_interval") or 60,
                )
            )
            await self.core._start_bounce_receiver()

    async def on_shutdown(self) -> None:
        if self._core is not None:
            await self.core.stop()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope["auth"] = await self.authenticate_request(scope)
        legacy = scope["type"] == "http" and scope.get("path", "").split("/")[1:2] == ["v1"]
        started = False

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await super().__call__(scope, receive, tracking_send)
        except HTTPException as exc:
            if started:
                raise
            status = 422 if isinstance(exc, HTTPBadRequest) else exc.status
            if legacy:
                # Kajenn's outer error middleware uses {"error": ...}; v1
                # clients require the FastAPI {"detail": ...} envelope even
                # without an Accept header. Keep structured rejection details.
                response = Response(
                    status_code=status,
                    headers=[
                        (name.decode("latin-1"), value.decode("latin-1"))
                        for name, value in exc.headers
                    ],
                )
                response.set_result({"detail": exc.detail})
                await response(scope, receive, send)
            elif isinstance(exc, HTTPBadRequest):
                raise HTTPException(status, exc.detail, headers=exc.headers) from exc
            else:
                raise

    async def authenticate_request(self, scope: Scope) -> Avatar | None:
        """Resolve an explicit API token, otherwise preserve the Kajenn identity."""
        token = headers_dict(scope).get(API_TOKEN_HEADER)
        admin_avatar = Avatar(ADMIN_IDENTITY, [ADMIN_TAG, TENANT_TAG])
        if not token:
            if self._allow_anonymous_admin and self.api_token is None:
                return admin_avatar
            avatar = scope.get("auth")
            if avatar is not None and "admin" in avatar.tags:
                return Avatar(avatar.identity, list(set(avatar.tags) | {ADMIN_TAG, TENANT_TAG}))
            return avatar
        if self.api_token is not None and secrets.compare_digest(token, self.api_token):
            return admin_avatar
        tenant = await self.core.db.tenants.get_tenant_by_token(token)
        if tenant:
            return Avatar(tenant["id"], [TENANT_TAG])
        return None

    async def discover(self, scope, **kwargs):
        # Resolve factories before enumeration: genro-routes lazy descriptors
        # omit auth/channel filters and typed schemas until materialized.
        kwargs["_eager"] = True
        return await super().discover(scope, **kwargs)

    def bind_kwargs(self, node: RouterNode, request: Request) -> dict[str, Any]:
        kwargs = super().bind_kwargs(node, request)
        fields = node.params.get("fields") or []
        if node.path.startswith("v1/"):
            # Legacy HTTP query booleans use Pydantic's accepted textual forms;
            # Kajenn's TYTX decoder only hydrates lowercase true/false itself.
            from pydantic import TypeAdapter, ValidationError

            for field in fields:
                name = field["name"]
                value = kwargs.get(name)
                if (
                    (field.get("schema") or {}).get("type") == "boolean"
                    and name in request.query
                    and (isinstance(value, str) or type(value) is int)
                ):
                    try:
                        kwargs[name] = TypeAdapter(bool).validate_python(value)
                    except ValidationError:
                        pass  # The route validator retains its normal 422 error.
        if any(field["name"] == "request" for field in fields):
            kwargs["request"] = request
        return kwargs

    def require_admin_tag(self, request: Request) -> None:
        """Refuse a request whose identity is not the admin one (403).

        The gate of the admin-only branches of a shim the router had to open to
        tenant tokens. The status is v1's: the token is real, its scope is not.
        """
        if ADMIN_TAG not in request.auth_tags:
            raise HTTPForbidden(
                "Admin token required, tenant tokens not allowed for this operation"
            )

    def require_tenant_scope(self, request: Request, tenant_id: str | None) -> None:
        """Refuse a tenant token that addresses another tenant's resources (401).

        Admin may address every tenant. Other identities must name their own
        tenant explicitly, including account creation and batch operations.
        """
        avatar = request.avatar()
        if avatar is None:
            raise HTTPUnauthorized("Authentication required")
        if ADMIN_TAG in avatar.tags:
            return
        if not tenant_id or avatar.identity != tenant_id:
            raise HTTPUnauthorized("Token not authorized for this tenant")

    def get_token_tenant_id(self, request: Request) -> str | None:
        """The tenant a tenant key names, or ``None`` for the admin identity.

        ``POST /commands/run-now`` deduces its tenant this way and from nowhere
        else, so an admin token wakes every tenant and a tenant key only its own.
        """
        avatar = request.avatar()
        if ADMIN_TAG in avatar.tags:
            return None
        return avatar.identity

    @route(channel_channels="rest")
    async def health(self) -> dict[str, str]:
        """Liveness probe of the process, no auth and no version.

        The container's healthcheck reads this one, so it survives a version
        branch being added or retired. Each version publishes its own.
        """
        return {"status": "ok"}
