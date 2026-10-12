"""Asqueel database registered in Kajenn and asynchronous mail facade."""

from asqueel import AsqueelDb

from .persistence.async_store import AsyncStore
from .persistence.mail_store import MailStore


class MailDatabase(AsqueelDb):
    """Configured runtime: construction declares the model without database I/O."""

    def __init__(self, connection_string):
        self.store = MailStore(connection_string)
        super().__init__(self.store.recipe())
        self.store._runtime = self


class MailProxyDb(AsyncStore):
    def __init__(self, connection_string="/data/mail_service.db", *, run_sync=None, runtime=None):
        store = runtime.store if runtime is not None else MailStore(connection_string)
        super().__init__(store, run_sync=run_sync, runtime=runtime)
