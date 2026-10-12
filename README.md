# kajenn-mail-proxy

An asynchronous email dispatcher built as a Kajenn `McpOpenApiApplication`.
Applications enqueue messages through REST or MCP; background workers deliver
email over SMTP and report results through HTTP callbacks.

## Features

- Persistent queue on SQLite or PostgreSQL, surviving restarts.
- Automatic retry with configurable delays, priorities and deferred delivery.
- Per-account rate limits and pooled SMTP connections.
- Multi-tenant accounts, credentials and message access.
- Delivery reports, event history, command logs and Prometheus metrics.
- IMAP bounce detection with DSN parsing and hard/soft classification.
- Italian PEC receipt tracking.
- Attachments from base64, local files, tenant endpoints or HTTP URLs.
- Memory/disk attachment cache and large-file offloading to external storage
  through fsspec, with warning, rejection or replacement by download links.

The SMTP and reporting pipeline remains asynchronous. Phase 1 migrates the
application lifecycle and REST/MCP integration to Kajenn; the existing SQL
persistence remains in use. The planned Asqueel persistence migration is **not
implemented yet**. See [MIGRATION_PLAN.md](MIGRATION_PLAN.md).

## Run locally

Python 3.11 or newer is required. Install from this checkout:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export GMP_DB_PATH=./mail_service.db
export GMP_API_TOKEN=replace-with-a-secret
uvicorn kajenn_mail_proxy.server:build_server --factory --host 0.0.0.0 --port 8000
```

`python main.py` launches the same factory and also accepts `GMP_HOST`,
`GMP_PORT` and `GMP_LOG_LEVEL`. The factory creates the engine and opens its
persistence during application startup, then stops the engine during shutdown.
Importing the server module does not create a global engine or open a database.

To build a local Docker image:

```bash
docker build -t kajenn-mail-proxy .
docker run --rm -p 8000:8000 \
  -e GMP_API_TOKEN=replace-with-a-secret \
  -e GMP_DB_PATH=/data/mail_service.db \
  -v mail-data:/data kajenn-mail-proxy
```

## API and authentication

- `/mailproxy/v1`: compatibility adapters for the existing REST contract.
- `/mailproxy/api`: typed application operations shared with MCP.
- `/mailproxy/mcp`: MCP endpoint for discovery and tool calls.
- `/mailproxy/health`: public liveness endpoint.

For example, enqueue messages with `POST /mailproxy/v1/commands/add-messages`,
list them with `GET /mailproxy/v1/messages?tenant_id=acme`, and read metrics at
`GET /mailproxy/v1/metrics`.

REST and MCP share authorization. Supply `X-API-Token` with the configured
admin token or a tenant token. Tenant identities are restricted to their own
resources; administrative operations require admin permissions. Without a
configured token, unauthenticated callers remain anonymous and do not become
administrators. Legacy anonymous administration requires the explicit factory
option `allow_anonymous_admin=True`; trusted Kajenn admin identities can also
receive application admin permissions.

## Configuration and CLI

The grammar and validated settings live in
[`configuration.py`](src/kajenn_mail_proxy/configuration.py). The factory builds
an application recipe containing database, engine, cache and bounce settings.
Engine environment variables use `GMP_` (for example,
`GMP_MAX_CONCURRENT_SENDS`); cache and bounce settings use `GMP_CACHE_` and
`GMP_BOUNCE_`. Explicit factory arguments override environment settings.
`GMP_RETRY_DELAYS` accepts a JSON list.

Tenant/account settings and runtime state remain persistent. Deployment bounce
settings seed an unconfigured instance; persisted runtime changes then take
precedence. Configure `GMP_API_TOKEN` explicitly: the standalone factory does
not implicitly recover an admin token from the database.

The command name stays `mail-proxy`:

```bash
mail-proxy list
mail-proxy start myserver
mail-proxy stop myserver
mail-proxy myserver info
mail-proxy myserver tenants list
mail-proxy myserver tenants add acme
mail-proxy myserver acme accounts list
mail-proxy myserver acme accounts add
mail-proxy myserver acme messages list
mail-proxy myserver acme send email.eml
mail-proxy myserver acme run-now
```

CLI instances retain their data at `~/.mail-proxy/<name>/mail_service.db`.
Optional dependencies can be installed from this checkout, for example
`pip install -e ".[postgresql,large-files,bounce]"`.

## Attachments

| `fetch_mode` | Example `storage_path` | Source |
| --- | --- | --- |
| `base64` | `base64:SGVsbG8=` | Inline content with a `base64:` prefix |
| `filesystem` | `/tmp/file.pdf` | Local file |
| `endpoint` | `doc_id=123` | HTTP POST to the tenant attachment endpoint |
| `http_url` | `https://storage.example.com/file.pdf` | HTTP GET |

Large-file handling is configured per tenant through `large_file_config`,
including `enabled`, `max_size_mb`, `storage_url` and `action` (`warn`, `reject`
or `rewrite`).

## Tests

```bash
pip install -e ".[dev]"
pytest
pytest tests/unit tests/local_e2e
pytest tests/unit tests/local_e2e -m "not pec and not fullstack"
```

External SMTP/IMAP, Docker and PostgreSQL checks require their corresponding
services. PEC tests are excluded by default; use `pytest -m pec` when configured.
See [tests/README.md](tests/README.md) and the migration baseline for environment
requirements and known limitations.

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

Copyright 2025–2026 Softwell S.r.l. — Genropy Team
