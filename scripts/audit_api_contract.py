#!/usr/bin/env python3
"""Check the engine's client against the official Bitunix API contract.

This is the guard that stops the class of bug that produced the 30029 SL-side
error, the 404 endpoint paths and the wrong parameter names. It parses
`jrock/trader/client.py` statically (AST, no network, no credentials) and
compares every `request(...)` call against `docs/bitunix_api_contract.json`.

For each documented endpoint it reports:
  - implemented / missing
  - for implemented ones: missing required params, wrong param names,
    undocumented extra params

Exit code is non-zero when anything is missing or mismatched, so the test suite
fails on a regression.

Usage:
    python scripts/audit_api_contract.py            # human report
    python scripts/audit_api_contract.py --json     # machine report
    python scripts/audit_api_contract.py --strict   # exit 1 on any finding
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(ROOT, "docs", "bitunix_api_contract.json")
CLIENT = os.path.join(ROOT, "jrock", "trader", "client.py")

# Endpoints the engine deliberately does not implement, with the reason. These
# are reported as OUT-OF-SCOPE rather than silently skipped.
OUT_OF_SCOPE = {
    "/api/v1/cp/asset/query": "copy-trading sub-account asset query - not a trading-agent concern",
    "/api/v1/cp/asset/transfer-to-sub-account": "copy-trading fund transfer - not a trading-agent concern",
    "/api/v1/cp/asset/transfer-to-main-account": "copy-trading fund transfer - not a trading-agent concern",
}

# Params the docs list but that the engine legitimately omits because they are
# optional and unused. Recorded so the report is honest about the gap.
KNOWN_OMISSIONS: dict[str, set[str]] = {}


def load_contract() -> dict:
    with open(CONTRACT, encoding="utf-8") as fh:
        return json.load(fh)


def _dict_keys(node: ast.AST) -> set[str]:
    """String keys of a dict literal, plus keys set via d[k] = v in a block."""
    keys: set[str] = set()
    if isinstance(node, ast.Dict):
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value)
    return keys


def _assigned_keys(fn: ast.AST, var: str) -> set[str]:
    """Every string key written into the local dict `var` inside function `fn`.

    Handles both `body = {"a": 1}` and the incremental `body["a"] = 1` form that
    adjust_position_margin uses.
    """
    keys: set[str] = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                if isinstance(t, ast.Name) and t.id == var:
                    keys |= _dict_keys(sub.value)
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    if t.value.id == var and isinstance(t.slice, ast.Constant):
                        if isinstance(t.slice.value, str):
                            keys.add(t.slice.value)
        # `body: dict[str, Any] = {...}` is an AnnAssign, not an Assign
        if isinstance(sub, ast.AnnAssign):
            t = sub.target
            if isinstance(t, ast.Name) and t.id == var and sub.value is not None:
                keys |= _dict_keys(sub.value)
    return keys


def parse_client_calls() -> list[dict]:
    """Extract every self.request(...) call from client.py via AST.

    For each call we resolve the params dict literal and, when the body is
    passed as a local variable (`body=body`), the keys written into that
    variable inside the enclosing function. This is what makes the check
    accurate instead of leaking one function's keys into every endpoint.
    """
    with open(CLIENT, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=CLIENT)

    calls: list[dict] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "request"):
                continue
            if len(node.args) < 2:
                continue
            method, path = node.args[0], node.args[1]
            if not (isinstance(method, ast.Constant) and isinstance(path, ast.Constant)):
                continue

            params: set[str] = set()
            body: set[str] = set()
            for kw in node.keywords:
                if kw.arg == "params":
                    params |= _dict_keys(kw.value)
                if kw.arg == "body":
                    if isinstance(kw.value, ast.Name):
                        body |= _assigned_keys(fn, kw.value.id)
                    else:
                        body |= _dict_keys(kw.value)

            calls.append(
                {
                    "func": fn.name,
                    "method": method.value,
                    "path": path.value,
                    "params": sorted(params),
                    "body": sorted(body),
                    "lineno": node.lineno,
                }
            )
    return calls


def parse_validated_passthroughs() -> dict[str, set[str]]:
    """Map path -> the required-param tuple a **kwargs passthrough validates.

    `place_order(**kwargs)` and `modify_order(**kwargs)` forward the caller's
    dict verbatim, so the AST cannot see the keys. They DO declare the official
    required set as a class constant and check it before sending. This reads
    that constant so the audit can verify the endpoint honestly instead of
    reporting a false "missing required".
    """
    with open(CLIENT, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=CLIENT)

    # class-level constants: NAME = ("a", "b")
    consts: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and isinstance(node.value, ast.Tuple):
                    vals = {e.value for e in node.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                    if vals:
                        consts[t.id] = vals

    out: dict[str, set[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # find the request() path this function calls
        path = None
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if sub.func.attr == "request" and len(sub.args) >= 2:
                    p = sub.args[1]
                    if isinstance(p, ast.Constant) and isinstance(p.value, str):
                        path = p.value
        if not path:
            continue
        # find which constant it iterates over for validation. The check is
        # written as a comprehension (`[k for k in self.X if ...]`), so look for
        # comprehensions as well as plain for-loops.
        for sub in ast.walk(fn):
            iters = []
            if isinstance(sub, ast.For):
                iters.append(sub.iter)
            if isinstance(sub, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                iters.extend(g.iter for g in sub.generators)
            for it in iters:
                if isinstance(it, ast.Attribute) and it.attr in consts:
                    out[path] = consts[it.attr]
    return out


def normalise(name: str) -> str:
    """Strip the docs' nested-object marker ('>qty' -> 'qty')."""
    return name.lstrip(">").strip()


def audit() -> dict:
    contract = load_contract()
    calls = parse_client_calls()
    passthrough = parse_validated_passthroughs()

    by_path: dict[str, list[dict]] = {}
    for c in calls:
        by_path.setdefault(c["path"], []).append(c)

    results = []
    for e in contract["endpoints"]:
        if e["kind"] != "rest" or not e["path"]:
            continue
        path = e["path"]
        # Nested object fields (the docs mark them ">qty") are children of a
        # list param, not top-level request params. Filter them out BEFORE
        # normalising, or the ">" marker is lost and they look top-level.
        doc_req_top = {p["name"] for p in e["request_params"]
                       if p["required"] and not p["name"].startswith(">")}
        doc_opt_top = {p["name"] for p in e["request_params"]
                       if not p["required"] and not p["name"].startswith(">")}

        impl = by_path.get(path)
        if not impl:
            results.append(
                {
                    "path": path,
                    "title": e["title"],
                    "method": e["method"],
                    "status": "OUT-OF-SCOPE" if path in OUT_OF_SCOPE else "MISSING",
                    "reason": OUT_OF_SCOPE.get(path, ""),
                    "doc_required": sorted(doc_req_top),
                    "doc_optional": sorted(doc_opt_top),
                }
            )
            continue

        c = impl[0]
        sent = set(c["params"]) | set(c["body"])
        # a **kwargs passthrough validates its required set from a constant
        validated = passthrough.get(path)
        if validated:
            sent |= validated

        missing_req = sorted(doc_req_top - sent)
        extra = sorted(sent - doc_req_top - doc_opt_top)
        # params the docs list as optional and the engine does not send
        unsent_opt = sorted(doc_opt_top - sent)

        status = "OK"
        if missing_req:
            status = "MISSING-REQUIRED"
        elif extra:
            status = "EXTRA-PARAM"

        results.append(
            {
                "path": path,
                "title": e["title"],
                "method": e["method"],
                "status": status,
                "client_method": c["method"],
                "client_lineno": c["lineno"],
                "doc_required": sorted(doc_req_top),
                "doc_optional": sorted(doc_opt_top),
                "sent": sorted(sent),
                "missing_required": missing_req,
                "extra": extra,
                "unsent_optional": unsent_opt,
            }
        )

    implemented = [r for r in results if r["status"] not in ("MISSING", "OUT-OF-SCOPE")]
    missing = [r for r in results if r["status"] == "MISSING"]
    oos = [r for r in results if r["status"] == "OUT-OF-SCOPE"]
    bad = [r for r in results if r["status"] in ("MISSING-REQUIRED", "EXTRA-PARAM")]

    return {
        "contract_endpoints": len([e for e in contract["endpoints"] if e["kind"] == "rest"]),
        "implemented": len(implemented),
        "missing": len(missing),
        "out_of_scope": len(oos),
        "mismatched": len(bad),
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any finding")
    args = ap.parse_args()

    rep = audit()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print("=" * 78)
        print("BITUNIX API CONTRACT AUDIT")
        print("=" * 78)
        print(f"documented REST endpoints : {rep['contract_endpoints']}")
        print(f"implemented               : {rep['implemented']}")
        print(f"missing                   : {rep['missing']}")
        print(f"deliberately out of scope : {rep['out_of_scope']}")
        print(f"parameter mismatches      : {rep['mismatched']}")
        print()
        for r in rep["results"]:
            if r["status"] == "OK":
                continue
            print(f"[{r['status']}] {r['method']} {r['path']}")
            if r.get("reason"):
                print(f"    reason: {r['reason']}")
            if r.get("missing_required"):
                print(f"    MISSING REQUIRED: {r['missing_required']}")
            if r.get("extra"):
                print(f"    UNDOCUMENTED EXTRA: {r['extra']}")
            print()

    if args.strict and (rep["missing"] or rep["mismatched"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
