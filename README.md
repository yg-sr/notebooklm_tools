# nblm — 個人 NotebookLM アップローダ

無料/個人アカウントの Google NotebookLM へ、**ファイル / URL / テキスト**をプログラムから
追加するための自作ツール。**CLI・Python ライブラリ・MCP サーバー**の3つの使い方を提供します。

> 公式 API は NotebookLM Enterprise 限定のため、本ツールは Google の内部 `batchexecute`
> RPC を利用する**非公式実装**です。予告なく動かなくなる可能性があります（個人利用向け）。

## 仕組みと前提（重要）

認証は **そのマシン上のログイン済み Chrome プロファイル**に紐づきます。

- 初回に `nblm login` で、専用の Chrome プロファイル（`~/.notebooklm/chrome-profile`）に
  一度だけ Google ログインします。
- 以降の各操作は、そのプロファイルをヘッドレスで開いて認証トークンを取得し、HTTP で
  NotebookLM の RPC を呼びます。
- **認証はマシンごと**です。別の PC で使うには、その PC でも下記セットアップ＋`nblm login`
  が必要です（後述）。

### 必要なもの
- PC（Mac Apple Silicon で動作確認済）
- [uv](https://docs.astral.sh/uv/)（`brew install uv`）
- **Google Chrome**（アプリ本体。`channel="chrome"` で実 Chrome を使うため）
- Python 3.10+（uv が自動調達）

## セットアップ

```bash
# このリポジトリのディレクトリで
brew install uv                       # 未導入なら
uv tool install --from . nblm         # `nblm` と `nblm-mcp` を PATH に入れる
nblm login                            # 専用 Chrome が開く → Google にログイン（初回のみ）
nblm list                             # ノート一覧が出れば成功
```

`nblm login` では専用ウィンドウが開きます。普段使いの Chrome とは別プロファイルなので、
普段の Chrome は開いたままで構いません。ログインを検知すると自動で閉じます。

## 1. CLI として使う

`nblm` はグローバルにインストールされ、どこからでも・他アプリからも呼べます。

```bash
nblm list [--json]                                   # ノート一覧
nblm create "タイトル" [--json]                       # ノート作成
nblm sources <NOTEBOOK_ID> [--json]                  # ノート内のソース一覧

# ソース追加（content は URL / ローカルパス / テキスト本文。type は自動判定）
nblm add "https://example.com"        -n <NOTEBOOK_ID> --wait
nblm add ./report.pdf                 -n <NOTEBOOK_ID> --wait
nblm add "メモ本文"                    -n <NOTEBOOK_ID> --type text --title "メモ"

nblm delete-source <SOURCE_ID> -n <NOTEBOOK_ID>      # ソース削除
nblm rename-source <SOURCE_ID> "新しい名前"           # ソース名変更
nblm delete-notebook <NOTEBOOK_ID>                   # ノート削除（中のソースも全削除）
```

主なオプション: `-n/--notebook`（対象ノート）, `--type auto|url|file|text`,
`--title`, `--wait`（取り込み完了まで待つ）, `--json`（機械可読出力）。
終了コードは成功 `0` / エラー `1`。

### 他アプリ・他言語から（subprocess 例）
```python
import subprocess, json
out = subprocess.run(["nblm", "list", "--json"], capture_output=True, text=True).stdout
notebooks = json.loads(out)
subprocess.run(["nblm", "add", "/path/to/file.pdf", "-n", notebooks[0]["id"], "--wait"])
```

cron / launchd / iOS ショートカット / Automator などからも、`nblm ...` を実行するだけで使えます。

## 2. Python ライブラリとして import して使う

別プロジェクトに取り込むには、その環境へインストールします（**同じマシン**であること）。

```bash
uv add /path/to/notebooklm_api        # uv プロジェクトの場合
# または: pip install /path/to/notebooklm_api
```

### 非同期（推奨：1セッションを使い回せて速い）
```python
import asyncio
from nblm import NotebookLM

async def main():
    async with NotebookLM() as nb:                 # 1回の Chrome 起動を全呼び出しで共有
        books = await nb.list_notebooks()
        nbid = books[0].id
        res = await nb.add("https://example.com", nbid, wait=True)
        print(res)                                 # {'source_id': ..., 'type': 'url', 'status': 'ready'}
        await nb.add("メモ本文", nbid, type="text", title="メモ")

asyncio.run(main())
```

### 同期（簡易スクリプト向け）
```python
from nblm import NotebookLMSync

nb = NotebookLMSync()
for b in nb.list_notebooks():
    print(b.id, b.title, b.sources)
nb.add("/path/to/file.pdf", "<NOTEBOOK_ID>", wait=True)
```

公開 API: `NotebookLM`（async）, `NotebookLMSync`（sync）, 低レベルの `NblmClient`,
データ型 `Notebook` / `Source`, 例外 `NotebookLMError`。

> 補足: 各呼び出しはヘッドレス Chrome を起動してトークンを取り直すため数秒かかります。
> async の `NotebookLM` を `async with` で使うと、その間は1セッションを再利用して高速です。

## 3. MCP サーバーとして使う（Claude Code 等の AI エージェント）

stdio で動く MCP サーバー `nblm-mcp` を提供します。公開ツール:
`list_notebooks` / `create_notebook` / `delete_notebook` / `add_source` /
`list_sources` / `delete_source` / `rename_source`。

Claude Code への登録（user スコープ）:
```bash
claude mcp add notebooklm -s user -- nblm-mcp
```
登録後 Claude Code を再起動すると、「この URL を NotebookLM の〇〇ノートに追加して」のように
依頼できます。

## 別の PC で使うには

認証はマシンごとなので、その PC で同じセットアップをすれば独立して使えます。

1. その PC に **Google Chrome** と **uv** をインストール
2. このプロジェクトを配置（git clone やコピー）
3. `uv tool install --from . nblm`
4. `nblm login`（その PC で一度ログイン）
5. 以降は CLI / ライブラリ / MCP を同様に利用可能

## 制限・注意

- **認証はマシンごと**（プロファイルは持ち運べない）。共有したい場合は、1台をサーバーにして
  HTTP API 化する構成が別途必要。
- Chrome プロファイルは**同時に1プロセスのみ**。CLI/MCP を並列に叩くとロック競合するので、
  連続実行は順番に。
- ライブラリの `add(..., type="file")` / CLI の file 追加は、**実行マシン上のローカルパス**を
  対象にします。
- 非公式・内部 API 依存のため、Google 側の変更で壊れる場合があります。レスポンス構造が
  変わったら `nblm list --json` / `nblm sources <id> --json` の生出力を見て調整してください。

## 構成

```
src/nblm/
  auth.py        専用 Chrome プロファイルから認証トークン+Cookie を取得 (mint_session)
  rpc.py         batchexecute のエンコード/デコード
  client.py      NblmClient: notebooks / sources / アップロード / ポーリング
  login.py       初回ログイン（永続 Chrome プロファイル）
  api.py         公開ライブラリ API（NotebookLM / NotebookLMSync）
  cli.py         `nblm` CLI
  mcp_server.py  `nblm-mcp` MCP サーバー（stdio）
```
