"""MCP server exposing the NotebookLM uploader to Claude Code and other clients.

Each tool opens a fresh authenticated session (cheap: one GET to scrape tokens)
and closes it, so there is no long-lived global client to keep healthy.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .auth import Session
from .client import NblmClient

mcp = FastMCP("notebooklm")


@mcp.tool()
async def list_notebooks() -> list[dict]:
    """自分の NotebookLM のノート一覧を返す。各要素は {id, title, sources}。"""
    async with Session() as s:
        notebooks = await NblmClient(s).list_notebooks()
    return [{"id": n.id, "title": n.title, "sources": n.sources} for n in notebooks]


@mcp.tool()
async def create_notebook(title: str) -> dict:
    """新しいノートを作成し {id, title} を返す。"""
    async with Session() as s:
        nb = await NblmClient(s).create_notebook(title)
    return {"id": nb.id, "title": nb.title}


@mcp.tool()
async def delete_notebook(notebook_id: str) -> dict:
    """ノートを削除する（中のソースもすべて消える）。戻り値: {deleted, notebook_id}。"""
    async with Session() as s:
        await NblmClient(s).delete_notebook(notebook_id)
    return {"deleted": True, "notebook_id": notebook_id}


@mcp.tool()
async def add_source(
    notebook_id: str,
    content: str,
    source_type: str = "auto",
    title: str | None = None,
    wait: bool = False,
) -> dict:
    """ノートにソースを追加する。

    content: ファイルパス / URL / テキスト本文。
    source_type: "auto"(既定) | "file" | "url" | "text"。
    title: ソース名（任意）。text の場合はタイトルになる。
    wait: True なら取り込み完了まで待つ。
    戻り値: {source_id, type, status}。
    """
    async with Session() as s:
        return await NblmClient(s).add_source(notebook_id, content, source_type, title, wait)


@mcp.tool()
async def list_sources(notebook_id: str) -> list[dict]:
    """ノート内のソース一覧を返す（検証用）。各要素は {id, title, status}。"""
    async with Session() as s:
        sources = await NblmClient(s).list_sources(notebook_id)
    return [{"id": x.id, "title": x.title, "status": x.status_name} for x in sources]


@mcp.tool()
async def delete_source(notebook_id: str, source_id: str) -> dict:
    """指定したソースをノートから削除する。戻り値: {deleted, source_id}。"""
    async with Session() as s:
        await NblmClient(s).delete_source(notebook_id, source_id)
    return {"deleted": True, "source_id": source_id}


@mcp.tool()
async def rename_source(source_id: str, new_title: str) -> dict:
    """ソース名を変更する。戻り値: {renamed, source_id, title}。"""
    async with Session() as s:
        await NblmClient(s).rename_source(source_id, new_title)
    return {"renamed": True, "source_id": source_id, "title": new_title}


def main() -> None:
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
