#!/usr/bin/env python3
"""Fetch the official Bitunix futures API reference and build a machine-readable contract.

This is the root-cause fix for the class of bug that produced the 30029 SL-side
error, the 404 endpoint paths and the wrong parameter names: the engine was built
from partial knowledge of the docs. This script reads the docs themselves.

It enumerates every endpoint page linked from the API reference nav, fetches each
one, and extracts:
  - HTTP method and exact path
  - every request parameter: name, type, required
  - every response field: name, type
  - the source URL and the fetch date

Output: docs/bitunix_api_contract.json (machine) and docs/bitunix_api_contract.md
(human). Run with --offline to rebuild from the cached raw pages in
docs/_raw_api_docs/ without touching the network.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.bitunix.com"
NAV_PAGE = BASE + "/api-docs/futures/market/get_kline.html"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "docs", "_raw_api_docs")
OUT_JSON = os.path.join(ROOT, "docs", "bitunix_api_contract.json")
OUT_MD = os.path.join(ROOT, "docs", "bitunix_api_contract.md")

# Pages that are prose, not endpoints. They are still fetched and recorded, but
# they carry no method/path and are marked kind="guide".
GUIDE_PAGES = {
    "/api-docs/futures/common/introduction.html",
    "/api-docs/futures/common/sign.html",
    "/api-docs/futures/ErrorCode/error_code.html",
    "/api-docs/futures/log/change_log.html",
    "/api-docs/futures/websocket/prepare/WebSocket.html",
}


def fetch(url: str, cache_name: str, offline: bool = False) -> str:
    """Fetch a page, caching the raw HTML under docs/_raw_api_docs/."""
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, cache_name)
    if offline:
        if not os.path.exists(path):
            raise FileNotFoundError(f"offline and no cached copy: {cache_name}")
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return body


def strip_tags(fragment: str) -> str:
    t = re.sub(r"<script.*?</script>", "", fragment, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_mod.unescape(t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def main_content(page: str) -> str:
    m = re.search(r"<main[^>]*>(.*?)</main>", page, re.S)
    return m.group(1) if m else page


def parse_tables(body: str) -> list[list[dict]]:
    """Return each <table> as a list of row dicts keyed by its header cells."""
    out = []
    for tbl in re.findall(r"<table.*?</table>", body, re.S):
        heads = re.findall(r"<th[^>]*>(.*?)</th>", tbl, re.S)
        heads = [strip_tags(h) for h in heads]
        if not heads:
            continue
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if not cells:
                continue
            cells = [strip_tags(c) for c in cells]
            if len(cells) != len(heads):
                continue
            rows.append(dict(zip(heads, cells)))
        if rows:
            out.append(rows)
    return out


def parse_endpoint(page: str, url: str, rel: str, nav_title: str = "") -> dict:
    body = main_content(page)
    text = strip_tags(body)

    title = nav_title
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
        if m:
            title = strip_tags(m.group(1)).replace("\u200b", "").strip()

    kind = "guide" if rel in GUIDE_PAGES else (
        "websocket" if "/websocket/" in rel else "rest"
    )

    # HTTP method + path, e.g. "GET /api/v1/futures/market/kline".
    # Guide pages (changelog, error codes) mention paths in prose, so they must
    # not be mined for one - only real endpoint pages carry a method+path.
    method = path = None
    if kind != "guide":
        m = re.search(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/api/v1/[A-Za-z0-9_/\-{}]+)", text)
        if m:
            method, path = m.group(1), m.group(2)

    # WebSocket channels have no HTTP path; record the channel name instead.
    # The subscribe example is the authoritative source ({"ch":"market_kline_1min"});
    # the prose form ("Channel name: depth_" / "Channel name is tpsl") is a
    # fallback. Both are matched against the UNESCAPED text, because the raw HTML
    # carries &quot; entities. Only websocket pages carry a channel.
    channel = None
    if kind == "websocket":
        m = re.search(r'"ch"\s*:\s*"([A-Za-z_0-9]+)"', text)
        if m:
            channel = m.group(1)
        else:
            m = re.search(r"Channel name(?:\s+is|:)\s+([A-Za-z_0-9]+)", text)
            if m:
                channel = m.group(1)

    tables = parse_tables(body)
    req_params: list[dict] = []
    resp_fields: list[dict] = []
    for rows in tables:
        keys = set(rows[0].keys())
        if {"Parameter", "Type", "Required"} <= keys:
            for r in rows:
                name = r["Parameter"].strip()
                # the docs use a literal "None" row to mean "no parameters"
                if name.lower() in ("none", "-", "n/a", ""):
                    continue
                req_params.append(
                    {
                        "name": name,
                        "type": r["Type"].strip(),
                        "required": r["Required"].strip().lower() == "true",
                        "description": r.get("Description", "").strip(),
                    }
                )
        elif {"Parameter", "Type"} <= keys and "Required" not in keys:
            for r in rows:
                name = r["Parameter"].strip()
                if name.lower() in ("none", "-", "n/a", ""):
                    continue
                resp_fields.append(
                    {
                        "name": name,
                        "type": r["Type"].strip(),
                        "description": r.get("Description", "").strip(),
                    }
                )

    rate_limit = None
    m = re.search(r"Rate Limit:\s*([^D]*?)\s*Description", text)
    if m:
        rate_limit = m.group(1).strip()

    return {
        "id": rel.replace(".html", "").replace("/", "."),
        "title": title,
        "kind": kind,
        "method": method,
        "path": path,
        "channel": channel,
        "rate_limit": rate_limit,
        "request_params": req_params,
        "response_fields": resp_fields,
        "source_url": url,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def enumerate_pages(offline: bool) -> list[tuple[str, str]]:
    """Return (relative_path, nav_title) for every endpoint page in the nav."""
    page = fetch(NAV_PAGE, "nav.html", offline)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a[^>]*href="(/api-docs/futures/[^"]+\.html)"[^>]*>(.*?)</a>', page, re.S
    ):
        rel, raw = m.group(1), m.group(2)
        if rel in seen:
            continue
        title = strip_tags(raw)
        # the prev/next footer links carry a "Previous page"/"Next page" prefix
        title = re.sub(r"^(Previous|Next) page", "", title).strip()
        seen.add(rel)
        out.append((rel, title))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="rebuild from cached raw pages")
    args = ap.parse_args()

    rels = enumerate_pages(args.offline)
    print(f"endpoint pages found in the nav: {len(rels)}")

    entries = []
    for i, (rel, nav_title) in enumerate(rels, 1):
        url = BASE + rel
        cache = rel.replace("/", "__")
        try:
            page = fetch(url, cache, args.offline)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i:2}/{len(rels)}] FAILED {rel}: {exc}")
            continue
        e = parse_endpoint(page, url, rel, nav_title)
        entries.append(e)
        tag = e["method"] or e["channel"] or e["kind"]
        print(f"  [{i:2}/{len(rels)}] {tag:12} {e['path'] or rel}")
        if not args.offline:
            time.sleep(0.15)

    contract = {
        "source": BASE + "/api-docs/futures/",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endpoint_count": len(entries),
        "endpoints": entries,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(contract, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    rest = [e for e in entries if e["kind"] == "rest"]
    ws = [e for e in entries if e["kind"] == "websocket"]
    guides = [e for e in entries if e["kind"] == "guide"]
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("# Bitunix Futures API contract\n\n")
        fh.write(f"Source: {contract['source']}\n\n")
        fh.write(f"Generated: {contract['generated_at']}\n\n")
        fh.write(
            f"**{len(entries)} pages** — {len(rest)} REST endpoints, "
            f"{len(ws)} WebSocket channels, {len(guides)} guides.\n\n"
        )
        fh.write("| # | Method | Path / channel | Required params | Optional |\n")
        fh.write("|---|---|---|---|---|\n")
        for i, e in enumerate(entries, 1):
            req = ", ".join(p["name"] for p in e["request_params"] if p["required"]) or "—"
            opt = ", ".join(p["name"] for p in e["request_params"] if not p["required"]) or "—"
            target = e["path"] or e["channel"] or e["title"] or e["id"]
            fh.write(f"| {i} | {e['method'] or e['kind']} | `{target}` | {req} | {opt} |\n")
        fh.write("\n## Per-endpoint detail\n\n")
        for e in entries:
            fh.write(f"### {e['title'] or e['id']}\n\n")
            fh.write(f"- Source: {e['source_url']}\n")
            fh.write(f"- Fetched: {e['fetched_at']}\n")
            if e["method"]:
                fh.write(f"- `{e['method']} {e['path']}`\n")
            if e["channel"]:
                fh.write(f"- channel: `{e['channel']}`\n")
            if e["rate_limit"]:
                fh.write(f"- rate limit: {e['rate_limit']}\n")
            if e["request_params"]:
                fh.write("\n| param | type | required |\n|---|---|---|\n")
                for p in e["request_params"]:
                    fh.write(f"| `{p['name']}` | {p['type']} | {p['required']} |\n")
            if e["response_fields"]:
                fh.write("\n| response field | type |\n|---|---|\n")
                for p in e["response_fields"]:
                    fh.write(f"| `{p['name']}` | {p['type']} |\n")
            fh.write("\n")

    print(f"\nwrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    print(f"total pages: {len(entries)}  rest: {len(rest)}  ws: {len(ws)}  guides: {len(guides)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
