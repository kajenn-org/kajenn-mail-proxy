Kajenn Mail Proxy
=================

An asynchronous email dispatcher built as a Kajenn ``McpOpenApiApplication``.
Applications enqueue messages through REST or MCP; background workers deliver
email over SMTP and report results through HTTP callbacks.

Features
--------

- Persistent queues on SQLite or PostgreSQL, managed through Asqueel.
- Automatic retry, priorities and deferred delivery.
- Per-account rate limits and pooled SMTP connections.
- Multi-tenant accounts, credentials and message access.
- Delivery reports, event history and Prometheus metrics.
- IMAP bounce detection and Italian PEC receipt tracking.
- Attachments from inline content, local files, tenant endpoints or HTTP URLs.

Architecture
------------

Kajenn hosts the application and exposes compatibility REST routes at
``/mailproxy/v1``, typed operations at ``/mailproxy/api`` and MCP at
``/mailproxy/mcp``. Authenticate using ``X-API-Token`` with an admin or tenant
token; unauthenticated requests do not receive admin permissions.

Each synchronous Asqueel database operation runs as a complete transaction in
Kajenn's thread pool. SMTP delivery and HTTP callbacks remain asynchronous.
See :doc:`architecture_overview` and :doc:`api_reference` for details.

Quick start
-----------

Use Python 3.11 or newer and install from the repository:

.. code-block:: bash

   git clone https://github.com/kajenn-org/kajenn-mail-proxy.git
   cd kajenn-mail-proxy
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   export GMP_DB_PATH=./mail_service.db
   export GMP_API_TOKEN=replace-with-a-secret
   uvicorn kajenn_mail_proxy.server:build_server --factory --port 8000

Then configure a tenant, add an SMTP account and enqueue messages.
See :doc:`installation` and :doc:`usage` for the complete procedure.

Command-line interface
----------------------

The command remains ``mail-proxy``:

.. code-block:: bash

   mail-proxy list
   mail-proxy start myserver
   mail-proxy myserver tenants add acme
   mail-proxy myserver acme accounts add
   mail-proxy myserver acme send email.eml

CLI instances store their data at ``~/.mail-proxy/<name>/mail_service.db``.
See :doc:`cli_reference` for commands and options.

Deployment and upgrades
-----------------------

Run one dispatcher per database. Multi-process queue coordination and
exactly-once delivery are not guaranteed. A legacy rate-log limitation can
share rate counts between accounts with the same ID in different tenants;
use globally unique account IDs.

For an existing installation, follow :doc:`asqueel-migration` before upgrading.
The :doc:`migration-baseline` records the original migration checks and known
limitations, including bounce correlation/reporting failures.

Source code and issues are available on
`GitHub <https://github.com/kajenn-org/kajenn-mail-proxy>`_.

.. toctree::
   :maxdepth: 2
   :hidden:

   overview
   features
   architecture_overview
   protocol
   installation
   network_requirements
   usage
   cli_reference
   attachments
   rate_limiting
   priority_queuing
   monitoring
   example_client
   integrations
   multi_tenancy
   pec
   api_reference
   faq
   modules
   contributing
   fullstack_testing
   fullstack_testing_reference
   asqueel-migration
   migration-baseline
