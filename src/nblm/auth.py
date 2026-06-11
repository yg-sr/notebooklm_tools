"""Authentication for personal NotebookLM via a live, logged-in browser profile.

Disk-extracted cookies authenticate the read API but not web navigation (the
fast-rotating ``__Secure-1PSIDTS`` goes stale and Google bounces navigation to
the account chooser), so we can't scrape the XSRF token (``at``) needed for
writes that way.

Instead we keep a dedicated, persistent **real Chrome** profile that the user
logs into once. For each session we open that profile headless, load the
authenticated NotebookLM page (the live session loads fine and refreshes its
tokens), and read ``SNlM0e`` (-> ``at``) and ``FdrFJe`` (-> ``f.sid``) plus the
fresh cookies. The actual RPC calls then run over plain httpx.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

BASE_URL = "https://notebooklm.google.com"

# Args that keep Chrome from advertising itself as automation-controlled, which
# is what triggers Google's "this browser may not be secure" sign-in block.
CHROME_ARGS = ["--disable-blink-features=AutomationControlled"]
CHROME_IGNORE_DEFAULT_ARGS = ["--enable-automation"]


def default_profile_dir() -> Path:
    override = os.environ.get("NBLM_PROFILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".notebooklm" / "chrome-profile"


def default_authuser() -> str:
    return os.environ.get("NBLM_AUTHUSER", "0")


class AuthError(RuntimeError):
    pass


@dataclass
class Tokens:
    csrf: str  # SNlM0e -> form field `at`
    sid: str   # FdrFJe -> query param `f.sid`


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def mint_session(profile_dir: Path):
    """Open the persistent Chrome profile headless and extract (Tokens, cookies).

    Returns ``(Tokens, list[cookie_dict])``. Raises :class:`AuthError` if the
    profile is not logged in.
    """
    from playwright.async_api import async_playwright

    if not profile_dir.exists():
        raise AuthError(
            f"ログイン済みプロファイルがありません: {profile_dir}\n"
            "先に `nblm login` を実行してください。"
        )

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chrome",
            headless=True,
            args=CHROME_ARGS,
            ignore_default_args=CHROME_IGNORE_DEFAULT_ARGS,
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(BASE_URL + "/", wait_until="domcontentloaded")
            at = None
            for _ in range(40):  # up to ~20s for the SPA to expose tokens
                if "accounts.google.com" in page.url:
                    raise AuthError(
                        "セッションが切れています。`nblm login` を再実行してください。"
                    )
                at = await page.evaluate(_JS_TOKEN.format(key="SNlM0e"))
                if at:
                    break
                await page.wait_for_timeout(500)
            if not at:
                raise AuthError(
                    "認証トークン (SNlM0e) を取得できませんでした。"
                    "`nblm login` でログインし直してください。"
                )
            sid = await page.evaluate(_JS_TOKEN.format(key="FdrFJe")) or ""
            cookies = await ctx.cookies()
            return Tokens(csrf=at, sid=sid), cookies
        finally:
            await ctx.close()


_JS_TOKEN = "() => (window.WIZ_global_data && window.WIZ_global_data['{key}']) || null"


def _build_jar(cookies: list[dict]) -> httpx.Cookies:
    jar = httpx.Cookies()
    for c in cookies:
        domain = c.get("domain") or ""
        if "google" not in domain:
            continue
        jar.set(c["name"], c["value"], domain=domain, path=c.get("path", "/"))
    return jar


class Session:
    """A live session: tokens minted from the Chrome profile + an httpx client.

    Usage::

        async with Session() as s:
            ...  # s.client, s.tokens, s.authuser
    """

    def __init__(self, profile_dir: Path | None = None, authuser: str | None = None):
        self.profile_dir = profile_dir or default_profile_dir()
        self.authuser = authuser or default_authuser()
        self.client: httpx.AsyncClient | None = None
        self.tokens: Tokens | None = None

    async def __aenter__(self) -> "Session":
        self.tokens, cookies = await mint_session(self.profile_dir)
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            cookies=_build_jar(cookies),
            follow_redirects=True,
            timeout=60.0,
            headers={"User-Agent": _USER_AGENT},
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
