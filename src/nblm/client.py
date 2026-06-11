"""High-level NotebookLM operations built on the batchexecute RPC.

All wire formats (rpcids, payload array shapes, response indices) were ported
from teng-lin/notebooklm-py. Array layouts can drift if Google changes the web
client; use ``--json`` on the CLI to dump raw results when debugging.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .auth import Session
from .rpc import RPCError, rpc_call

# --- rpcids ---------------------------------------------------------------
RPC_LIST_NOTEBOOKS = "wXbhsf"
RPC_CREATE_NOTEBOOK = "CCqFvf"
RPC_DELETE_NOTEBOOK = "WWINqb"
RPC_GET_NOTEBOOK = "rLM1Ne"
RPC_ADD_SOURCE = "izAoDd"
RPC_ADD_SOURCE_FILE = "o4cbdc"
RPC_UPDATE_SOURCE = "b7Wfje"
RPC_DELETE_SOURCE = "tGMBJ"

UPLOAD_PATH = "/upload/_/"

# Source ingestion status codes.
STATUS_PROCESSING = 1
STATUS_READY = 2
STATUS_ERROR = 3
STATUS_PREPARING = 5

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

_UNSUPPORTED_UPLOAD_EXTS = {".html", ".htm", ".xhtml"}


class NotebookLMError(RuntimeError):
    pass


@dataclass
class Notebook:
    id: str
    title: str
    sources: int = 0

    @classmethod
    def from_entry(cls, entry: list[Any]) -> "Notebook":
        title = entry[0] if len(entry) > 0 and isinstance(entry[0], str) else ""
        title = title.replace("thought\n", "").strip()
        sources = len(entry[1]) if len(entry) > 1 and isinstance(entry[1], list) else 0
        nb_id = entry[2] if len(entry) > 2 and isinstance(entry[2], str) else ""
        return cls(id=nb_id, title=title, sources=sources)


@dataclass
class Source:
    id: str
    title: str
    status: int | None = None
    raw: Any = field(default=None, repr=False)

    @property
    def status_name(self) -> str:
        return {
            STATUS_PROCESSING: "processing",
            STATUS_READY: "ready",
            STATUS_ERROR: "error",
            STATUS_PREPARING: "preparing",
        }.get(self.status, str(self.status))


def _find_uuid(obj: Any) -> str | None:
    """Recursively find the first UUID-looking string (used for source ids)."""
    if isinstance(obj, str):
        m = _UUID_RE.search(obj)
        return m.group(0) if m else None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = _find_uuid(item)
            if found:
                return found
    if isinstance(obj, dict):
        for item in obj.values():
            found = _find_uuid(item)
            if found:
                return found
    return None


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


class NblmClient:
    """Thin async facade over a :class:`Session`."""

    def __init__(self, session: Session):
        self._s = session

    # --- notebooks --------------------------------------------------------
    async def list_notebooks(self) -> list[Notebook]:
        result = await rpc_call(self._s, RPC_LIST_NOTEBOOKS, [None, 1, None, [2]])
        if not result:
            return []
        rows = result[0] if isinstance(result, list) else None
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise NotebookLMError(f"想定外のノート一覧レスポンス: {result!r}")
        return [Notebook.from_entry(r) for r in rows if isinstance(r, list)]

    async def create_notebook(self, title: str) -> Notebook:
        result = await rpc_call(
            self._s, RPC_CREATE_NOTEBOOK, [title, None, None, [2], [1]]
        )
        if not isinstance(result, list):
            raise NotebookLMError(f"想定外のノート作成レスポンス: {result!r}")
        return Notebook.from_entry(result)

    async def delete_notebook(self, notebook_id: str) -> None:
        await rpc_call(self._s, RPC_DELETE_NOTEBOOK, [[notebook_id], [2]])

    # --- sources: add -----------------------------------------------------
    async def add_url(self, notebook_id: str, url: str) -> str:
        if _youtube_id(url):
            params = [
                [[None, None, None, None, None, None, None, [url], None, None, 1]],
                notebook_id,
                [2],
                [1, None, None, None, None, None, None, None, None, None, [1]],
            ]
        else:
            params = [
                [[None, None, [url], None, None, None, None, None]],
                notebook_id,
                [2],
                None,
                None,
            ]
        result = await rpc_call(self._s, RPC_ADD_SOURCE, params)
        return self._source_id_or_lookup(result, notebook_id, match_url=url)

    async def add_text(self, notebook_id: str, title: str, content: str) -> str:
        params = [
            [[None, [title, content], None, None, None, None, None, None]],
            notebook_id,
            [2],
            None,
            None,
        ]
        result = await rpc_call(self._s, RPC_ADD_SOURCE, params)
        return self._source_id_or_lookup(result, notebook_id, match_title=title)

    async def add_file(self, notebook_id: str, file_path: Path) -> str:
        if file_path.suffix.lower() in _UNSUPPORTED_UPLOAD_EXTS:
            raise NotebookLMError(
                f"このファイル形式はアップロードできません: {file_path.suffix}"
            )
        if not file_path.is_file():
            raise NotebookLMError(f"ファイルが見つかりません: {file_path}")

        filename = file_path.name
        size = file_path.stat().st_size
        content_type = _guess_mime(file_path)

        # (1) register the source slot
        reg = await rpc_call(
            self._s,
            RPC_ADD_SOURCE_FILE,
            [
                [[filename]],
                notebook_id,
                [2],
                [1, None, None, None, None, None, None, None, None, None, [1]],
            ],
        )
        source_id = _find_uuid(reg)
        if not source_id:
            raise NotebookLMError(f"ファイル登録のレスポンスから source_id を取得できません: {reg!r}")

        # (2) open a resumable upload session
        upload_url = await self._start_upload(
            notebook_id, filename, size, source_id, content_type
        )
        # (3) stream + finalize
        await self._upload_bytes(upload_url, file_path)
        return source_id

    async def _start_upload(
        self, notebook_id: str, filename: str, size: int, source_id: str, content_type: str
    ) -> str:
        assert self._s.client is not None
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://notebooklm.google.com",
            "Referer": "https://notebooklm.google.com/",
            "x-goog-authuser": self._s.authuser,
            "x-goog-upload-command": "start",
            "x-goog-upload-header-content-length": str(size),
            "x-goog-upload-header-content-type": content_type,
            "x-goog-upload-protocol": "resumable",
        }
        body = json.dumps(
            {"PROJECT_ID": notebook_id, "SOURCE_NAME": filename, "SOURCE_ID": source_id}
        )
        resp = await self._s.client.post(UPLOAD_PATH, headers=headers, content=body)
        if resp.status_code != 200:
            raise NotebookLMError(
                f"アップロードセッション開始に失敗 (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        upload_url = resp.headers.get("x-goog-upload-url")
        if not upload_url:
            raise NotebookLMError("レスポンスに x-goog-upload-url がありません")
        return upload_url

    async def _upload_bytes(self, upload_url: str, file_path: Path) -> None:
        assert self._s.client is not None
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "x-goog-authuser": self._s.authuser,
            "x-goog-upload-command": "upload, finalize",
            "x-goog-upload-offset": "0",
        }
        data = file_path.read_bytes()
        resp = await self._s.client.post(upload_url, headers=headers, content=data)
        if resp.status_code != 200:
            raise NotebookLMError(
                f"ファイル本体のアップロードに失敗 (HTTP {resp.status_code}): {resp.text[:300]}"
            )

    async def rename_source(self, source_id: str, new_title: str) -> None:
        await rpc_call(
            self._s, RPC_UPDATE_SOURCE, [None, [source_id], [[[new_title]]]]
        )

    async def delete_source(self, notebook_id: str, source_id: str) -> None:
        await rpc_call(
            self._s,
            RPC_DELETE_SOURCE,
            [[[source_id]]],
            source_path=f"/notebook/{notebook_id}",
        )

    async def add_source(
        self,
        notebook_id: str,
        content: str,
        source_type: str = "auto",
        title: str | None = None,
        wait: bool = False,
    ) -> dict:
        """Add a source, auto-detecting file/url/text. Returns {source_id, type, status}."""
        stype = source_type if source_type != "auto" else detect_source_type(content)
        if stype == "url":
            source_id = await self.add_url(notebook_id, content)
        elif stype == "file":
            source_id = await self.add_file(notebook_id, Path(content).expanduser())
        elif stype == "text":
            source_id = await self.add_text(notebook_id, title or "Untitled text", content)
        else:
            raise NotebookLMError(f"不明な source_type: {source_type}")

        if title and stype != "text":
            await self.rename_source(source_id, title)

        status = "processing"
        if wait:
            src = await self.wait_until_ready(notebook_id, source_id)
            status = src.status_name
        return {"source_id": source_id, "type": stype, "status": status}

    # --- sources: read / poll --------------------------------------------
    async def list_sources(self, notebook_id: str) -> list[Source]:
        result = await rpc_call(
            self._s, RPC_GET_NOTEBOOK, [notebook_id, None, [2], None, 0]
        )
        rows = _source_rows(result)
        return [_parse_source_row(r) for r in rows]

    async def get_source(self, notebook_id: str, source_id: str) -> Source | None:
        for src in await self.list_sources(notebook_id):
            if src.id == source_id:
                return src
        return None

    async def wait_until_ready(
        self,
        notebook_id: str,
        source_id: str,
        timeout: float = 120.0,
        initial: float = 1.0,
        backoff: float = 1.5,
        max_interval: float = 10.0,
    ) -> Source:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        interval = initial
        while True:
            src = await self.get_source(notebook_id, source_id)
            if src is not None:
                if src.status == STATUS_READY:
                    return src
                if src.status == STATUS_ERROR:
                    raise NotebookLMError(
                        f"ソースの取り込みに失敗しました (source_id={source_id})"
                    )
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise NotebookLMError(
                    f"取り込み完了待ちがタイムアウトしました (source_id={source_id})"
                )
            await asyncio.sleep(min(interval, remaining))
            interval = min(interval * backoff, max_interval)

    # --- helpers ----------------------------------------------------------
    def _source_id_or_lookup(
        self,
        result: Any,
        notebook_id: str,
        match_url: str | None = None,
        match_title: str | None = None,
    ) -> str:
        sid = _find_uuid(result)
        if sid:
            return sid
        # Could not read it straight from the add response; surface the raw payload.
        raise NotebookLMError(
            "ソース追加のレスポンスから source_id を取得できませんでした。"
            f" 生レスポンス: {result!r}"
        )


# --- module-level helpers -------------------------------------------------
def detect_source_type(content: str) -> str:
    """Classify a source argument as url / file / text."""
    if content.startswith(("http://", "https://")):
        return "url"
    if Path(content).expanduser().is_file():
        return "file"
    return "text"


def _youtube_id(url: str) -> str | None:
    m = re.search(
        r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/|live/|v/)|youtu\.be/|"
        r"music\.youtube\.com/watch\?v=)([A-Za-z0-9_-]{11})",
        url,
    )
    return m.group(1) if m else None


def _source_rows(notebook_result: Any) -> list[Any]:
    """Sources live at notebook[0][1] in the GET_NOTEBOOK response."""
    try:
        rows = notebook_result[0][1]
    except (TypeError, IndexError, KeyError):
        return []
    return rows if isinstance(rows, list) else []


def _parse_source_row(src: Any) -> Source:
    sid = _find_uuid(src[0]) if len(src) > 0 else None
    if not sid:
        sid = _find_uuid(src) or ""
    title = src[1] if len(src) > 1 and isinstance(src[1], str) else ""
    status = None
    if len(src) > 3 and isinstance(src[3], list) and len(src[3]) > 1:
        if isinstance(src[3][1], int):
            status = src[3][1]
    return Source(id=sid, title=title, status=status, raw=src)
