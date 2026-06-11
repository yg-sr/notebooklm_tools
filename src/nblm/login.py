"""One-time interactive login into a dedicated, persistent Chrome profile.

Uses the real Chrome binary (``channel="chrome"``) so Google is far less likely
to block sign-in with "this browser may not be secure". The profile persists at
``~/.notebooklm/chrome-profile`` and is reused (headless) for every later
operation — see :func:`nblm.auth.mint_session`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .auth import (
    BASE_URL,
    CHROME_ARGS,
    CHROME_IGNORE_DEFAULT_ARGS,
    default_profile_dir,
)


class LoginError(RuntimeError):
    pass


def _logged_in(url: str) -> bool:
    return "notebooklm.google.com" in url and "accounts.google.com" not in url


async def persistent_login(profile_dir: Path | None = None, timeout: float = 300.0) -> Path:
    from playwright.async_api import async_playwright

    profile_dir = profile_dir or default_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(
        "\n専用 Chrome ウィンドウが開きます。Google にログインし、NotebookLM の\n"
        "ノート一覧が表示される状態にしてください。ログインを検知すると自動で\n"
        f"完了します（最大 {int(timeout)} 秒）。ウィンドウは閉じずにお待ちください...\n"
    )

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chrome",
            headless=False,
            args=CHROME_ARGS,
            ignore_default_args=CHROME_IGNORE_DEFAULT_ARGS,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(BASE_URL + "/")

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        ok = False
        while loop.time() < deadline:
            try:
                at = await page.evaluate(
                    "() => (window.WIZ_global_data && window.WIZ_global_data['SNlM0e']) || null"
                )
                url = page.url
            except Exception:
                # Page mid-navigation or window closed.
                if not ctx.pages:
                    break
                await asyncio.sleep(2.0)
                continue
            if at and _logged_in(url):
                ok = True
                break
            await asyncio.sleep(2.0)

        try:
            await ctx.close()
        except Exception:
            pass

    if not ok:
        raise LoginError(
            "時間内にログインを検知できませんでした。もう一度 `nblm login` を実行してください。"
        )

    print(f"\nログイン済みプロファイルを保存しました: {profile_dir}")
    return profile_dir
