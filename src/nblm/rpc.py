"""The Google ``batchexecute`` RPC protocol used by NotebookLM's web client.

Request:  f.req = [[[rpc_id, json.dumps(params), null, "generic"]]]  (params is
          double-serialized), posted as form-urlencoded with a trailing `&` and
          the CSRF token in `at`.
Response: a `)]}'`-prefixed, length-delimited chunk stream. We strip the prefix,
          parse the chunks, and return index 2 of the matching `wrb.fr` frame.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from .auth import Session

BATCHEXECUTE_PATH = "/_/LabsTailwindUi/data/batchexecute"

_ANTI_XSSI = re.compile(r"^\)\]\}'\r?\n")


class RPCError(RuntimeError):
    pass


def _encode_body(rpc_id: str, params: Any, csrf: str) -> bytes:
    params_json = json.dumps(params, separators=(",", ":"))
    f_req = json.dumps([[[rpc_id, params_json, None, "generic"]]], separators=(",", ":"))
    body = f"f.req={quote(f_req, safe='')}&at={quote(csrf, safe='')}&"
    return body.encode("utf-8")


def _parse_chunks(text: str) -> list[Any]:
    """Parse the length-delimited chunk stream into JSON records."""
    text = _ANTI_XSSI.sub("", text, count=1)
    records: list[Any] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        # A bare integer line announces the byte length of the next JSON record.
        if line.isdigit():
            i += 1
            if i < len(lines):
                try:
                    records.append(json.loads(lines[i]))
                except json.JSONDecodeError:
                    pass
            i += 1
        else:
            # Fallback: the line itself is JSON.
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
            i += 1
    return records


def _extract_result(records: list[Any], rpc_id: str) -> Any:
    """Return index 2 of the matching `wrb.fr` frame (last non-null wins)."""
    result: Any = None
    found = False
    for rec in records:
        if not isinstance(rec, list):
            continue
        for frame in rec:
            if (
                isinstance(frame, list)
                and len(frame) > 2
                and frame[0] == "wrb.fr"
                and frame[1] == rpc_id
            ):
                found = True
                payload = frame[2]
                if payload is None:
                    continue
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        pass
                result = payload
    if not found:
        raise RPCError(f"レスポンスに rpcid={rpc_id} のフレームがありません")
    return result


async def rpc_call(session: Session, rpc_id: str, params: Any, source_path: str = "/") -> Any:
    """Execute a single batchexecute RPC and return its decoded result payload."""
    assert session.client is not None and session.tokens is not None
    query = {
        "rpcids": rpc_id,
        "source-path": source_path,
        "f.sid": session.tokens.sid,
        "hl": "en",
        "rt": "c",
    }
    if session.authuser:
        query["authuser"] = session.authuser
    body = _encode_body(rpc_id, params, session.tokens.csrf)
    resp = await session.client.post(
        BATCHEXECUTE_PATH,
        params=query,
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Same-Domain": "1",
            "Origin": "https://notebooklm.google.com",
            "Referer": "https://notebooklm.google.com/",
        },
    )
    if resp.status_code != 200:
        raise RPCError(
            f"batchexecute {rpc_id} が HTTP {resp.status_code} を返しました: "
            f"{resp.text[:300]}"
        )
    records = _parse_chunks(resp.text)
    return _extract_result(records, rpc_id)
