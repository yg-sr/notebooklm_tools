"""The `nblm` command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .auth import AuthError, Session
from .client import NblmClient, NotebookLMError
from .login import LoginError


async def _cmd_login(args: argparse.Namespace) -> int:
    from .login import persistent_login

    await persistent_login()
    # Verify it worked.
    try:
        async with Session() as s:
            notebooks = await NblmClient(s).list_notebooks()
        print(f"ログイン成功。{len(notebooks)} 件のノートが見えています。")
    except (AuthError, NotebookLMError) as exc:
        print(f"警告: ログインは保存されましたが疎通確認に失敗しました:\n{exc}", file=sys.stderr)
        return 1
    return 0


async def _cmd_list(args: argparse.Namespace) -> int:
    async with Session() as s:
        notebooks = await NblmClient(s).list_notebooks()
    if args.json:
        print(json.dumps([n.__dict__ for n in notebooks], ensure_ascii=False, indent=2))
    else:
        if not notebooks:
            print("(ノートがありません)")
        for n in notebooks:
            print(f"{n.id}\t{n.sources:>3} sources\t{n.title}")
    return 0


async def _cmd_create(args: argparse.Namespace) -> int:
    async with Session() as s:
        nb = await NblmClient(s).create_notebook(args.title)
    if args.json:
        print(json.dumps(nb.__dict__, ensure_ascii=False))
    else:
        print(f"作成しました: {nb.id}\t{nb.title}")
    return 0


async def _cmd_add(args: argparse.Namespace) -> int:
    if not args.notebook:
        print("エラー: -n/--notebook でノート ID を指定してください。", file=sys.stderr)
        return 2

    async with Session() as s:
        res = await NblmClient(s).add_source(
            args.notebook, args.content, args.type, args.title, args.wait
        )

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"追加しました: source_id={res['source_id']} type={res['type']} status={res['status']}")
    return 0


async def _cmd_sources(args: argparse.Namespace) -> int:
    async with Session() as s:
        sources = await NblmClient(s).list_sources(args.notebook)
    if args.json:
        print(json.dumps(
            [{"id": s.id, "title": s.title, "status": s.status_name} for s in sources],
            ensure_ascii=False, indent=2,
        ))
    else:
        if not sources:
            print("(ソースがありません)")
        for src in sources:
            print(f"{src.id}\t{src.status_name}\t{src.title}")
    return 0


async def _cmd_delete_source(args: argparse.Namespace) -> int:
    async with Session() as s:
        await NblmClient(s).delete_source(args.notebook, args.source_id)
    print(f"削除しました: source_id={args.source_id}")
    return 0


async def _cmd_rename_source(args: argparse.Namespace) -> int:
    async with Session() as s:
        await NblmClient(s).rename_source(args.source_id, args.title)
    print(f"改名しました: source_id={args.source_id} -> {args.title}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nblm", description="Personal NotebookLM uploader")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="専用 Chrome プロファイルにログイン (一度だけ)")

    pl = sub.add_parser("list", help="ノート一覧")
    pl.add_argument("--json", action="store_true")

    pc = sub.add_parser("create", help="ノートを作成")
    pc.add_argument("title")
    pc.add_argument("--json", action="store_true")

    pa = sub.add_parser("add", help="ソースを追加 (file/url/text)")
    pa.add_argument("content", help="ファイルパス / URL / テキスト本文")
    pa.add_argument("-n", "--notebook", help="対象ノート ID")
    pa.add_argument("--type", choices=["auto", "file", "url", "text"], default="auto")
    pa.add_argument("--title", help="ソースのタイトル")
    pa.add_argument("--wait", action="store_true", help="取り込み完了まで待つ")
    pa.add_argument("--json", action="store_true")

    ps = sub.add_parser("sources", help="ノート内のソース一覧")
    ps.add_argument("notebook", help="ノート ID")
    ps.add_argument("--json", action="store_true")

    pd = sub.add_parser("delete-source", help="ソースを削除")
    pd.add_argument("source_id", help="削除するソース ID")
    pd.add_argument("-n", "--notebook", required=True, help="対象ノート ID")

    pr = sub.add_parser("rename-source", help="ソース名を変更")
    pr.add_argument("source_id", help="対象ソース ID")
    pr.add_argument("title", help="新しいソース名")

    return p


_HANDLERS = {
    "login": _cmd_login,
    "list": _cmd_list,
    "create": _cmd_create,
    "add": _cmd_add,
    "sources": _cmd_sources,
    "delete-source": _cmd_delete_source,
    "rename-source": _cmd_rename_source,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = _HANDLERS[args.command]
    try:
        return asyncio.run(handler(args))
    except (AuthError, NotebookLMError, LoginError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
