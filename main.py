# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Launch the standalone Kajenn mail application through its ASGI factory."""
from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "kajenn_mail_proxy.server:build_server",
        factory=True,
        host=os.environ.get("GMP_HOST", "0.0.0.0"),
        port=int(os.environ.get("GMP_PORT", "8000")),
        log_level=os.environ.get("GMP_LOG_LEVEL", "info").lower(),
    )
