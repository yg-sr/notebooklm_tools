"""Public library API for importing into other apps.

Async (recommended)::

    import asyncio
    from nblm import NotebookLM

    async def main():
        async with NotebookLM() as nb:          # one browser session, reused
            books = await nb.list_notebooks()
            await nb.add("https://example.com", books[0].id, wait=True)
    asyncio.run(main())

Sync (for non-async scripts)::

    from nblm import NotebookLMSync
    nb = NotebookLMSync()
    print(nb.list_notebooks())
    nb.add("note text", "<notebook-id>", type="text", title="memo")

Auth lives in the dedicated Chrome profile created by ``nblm login`` on this
machine; the importing app must run on that same machine (and have Chrome +
Playwright available). For other machines, run ``nblm login`` there too.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from .auth import Session
from .client import NblmClient, Notebook, Source


class NotebookLM:
    """Async client. Use as an async context manager to reuse one session
    (a single headless-Chrome token mint) across many calls, or call methods
    directly for one-off operations (each opens its own session)."""

    def __init__(self, profile_dir: Path | str | None = None, authuser: str | None = None):
        self._profile_dir = Path(profile_dir).expanduser() if profile_dir else None
        self._authuser = authuser
        self._session: Session | None = None

    async def __aenter__(self) -> "NotebookLM":
        self._session = await Session(self._profile_dir, self._authuser).__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session is not None:
            await self._session.__aexit__(*exc)
            self._session = None

    @asynccontextmanager
    async def _client(self):
        if self._session is not None:
            yield NblmClient(self._session)
        else:
            async with Session(self._profile_dir, self._authuser) as s:
                yield NblmClient(s)

    async def list_notebooks(self) -> list[Notebook]:
        async with self._client() as c:
            return await c.list_notebooks()

    async def create_notebook(self, title: str) -> Notebook:
        async with self._client() as c:
            return await c.create_notebook(title)

    async def add(
        self,
        content: str,
        notebook_id: str,
        type: str = "auto",
        title: str | None = None,
        wait: bool = False,
    ) -> dict:
        """Add a source (file path / URL / text). Returns {source_id, type, status}."""
        async with self._client() as c:
            return await c.add_source(notebook_id, content, type, title, wait)

    async def list_sources(self, notebook_id: str) -> list[Source]:
        async with self._client() as c:
            return await c.list_sources(notebook_id)


class NotebookLMSync:
    """Synchronous convenience wrapper. Each call runs its own event loop and
    opens its own session, so for many operations prefer the async
    :class:`NotebookLM` as a context manager."""

    def __init__(self, profile_dir: Path | str | None = None, authuser: str | None = None):
        self._nb = NotebookLM(profile_dir, authuser)

    def list_notebooks(self) -> list[Notebook]:
        return asyncio.run(self._nb.list_notebooks())

    def create_notebook(self, title: str) -> Notebook:
        return asyncio.run(self._nb.create_notebook(title))

    def add(
        self,
        content: str,
        notebook_id: str,
        type: str = "auto",
        title: str | None = None,
        wait: bool = False,
    ) -> dict:
        return asyncio.run(self._nb.add(content, notebook_id, type, title, wait))

    def list_sources(self, notebook_id: str) -> list[Source]:
        return asyncio.run(self._nb.list_sources(notebook_id))
