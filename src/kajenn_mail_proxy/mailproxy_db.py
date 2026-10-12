# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Stable import for the asynchronous facade over Asqueel persistence."""

from .asqueel_db import MailProxyDb

Persistence = MailProxyDb

__all__ = ["MailProxyDb", "Persistence"]
