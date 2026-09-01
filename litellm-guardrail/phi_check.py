#!/usr/bin/env python3
"""
phi-check — ask the gateway whether a payload would be blocked, before sending it.

Uses LiteLLM's existing POST /guardrails/apply_guardrail, so there is no custom
route to maintain and it survives platform upgrades. No model is called and no
tokens are spent.

WHY THIS EXISTS
---------------
Without it, a developer discovers the data boundary when their request fails at
runtime. With it, they can assert it in a unit test:

    assert phi_check(note)["allowed"] is False

That turns a governance control from something that blocks you into something
you can design against.

USAGE
    export PHI_GATEWAY=http://localhost:4000
    export PHI_API_KEY=sk-...

    phi_check.py "Patient Marcus Delgado, MRN 4471822."      # one string
    phi_check.py --file note.txt                             # a file
    cat note.txt | phi_check.py                              # stdin

    # CI: exit 1 if the payload would be blocked
    phi_check.py --file fixtures/*.txt --fail-on-block

EXIT CODES
    0  allowed (or blocked, without --fail-on-block)
    1  blocked, with --fail-on-block
    2  could not reach the gateway / bad configuration
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY = os.getenv("PHI_GATEWAY", "http://localhost:4000").rstrip("/")
API_KEY = os.getenv("PHI_API_KEY", "")
GUARDRAIL = os.getenv("PHI_GUARDRAIL_NAME", "phi-lane-guard")


def _parse_detail(raw: str) -> dict:
    """The gateway returns the denial detail as a Python repr, not JSON.

    So `True` rather than `true`, single quotes throughout. json.loads fails on
    it; ast.literal_eval handles it safely (it evaluates literals only, never
    arbitrary code). Try JSON first in case a future version fixes this.
    """
    for parse in (json.loads, ast.literal_eval):
        try:
            out = parse(raw)
            if isinstance(out, dict):
                return out
        except (ValueError, SyntaxError):
            continue
    return {"message": raw}


def phi_check(text: str, model: str = "") -> dict:
    """Return a normalised verdict for a payload.

    {
      "allowed": bool,
      "data_class": "phi" | "public",
      "detected_entities": [...],
      "suggested_model": str | None,
      "reason_code": str | None,
    }
    """
    body = {"guardrail_name": GUARDRAIL, "text": text}
    if model:
        body["model"] = model

    req = urllib.request.Request(
        f"{GATEWAY}/guardrails/apply_guardrail",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        return {
            "allowed": True,
            "data_class": "public",
            "detected_entities": [],
            "suggested_model": None,
            "reason_code": None,
        }
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise
        payload = json.loads(exc.read().decode())
        detail = _parse_detail(payload.get("error", {}).get("message", "{}"))
        return {
            "allowed": False,
            "data_class": detail.get("data_class", "phi"),
            "detected_entities": detail.get("detected_entities", []),
            "suggested_model": detail.get("suggested_model"),
            "required_lane": detail.get("required_lane"),
            "reason_code": detail.get("reason_code"),
            "how_to_fix": detail.get("how_to_fix"),
        }


def _render(label: str, v: dict) -> None:
    if v["allowed"]:
        print(f"\033[32m  ALLOWED\033[0m  {label}")
        print("           no identifiers detected — routes on quality and cost")
    else:
        print(f"\033[31m  BLOCKED\033[0m  {label}")
        print(f"           {v['reason_code']} · detected: "
              f"{', '.join(v['detected_entities']) or 'n/a'}")
        if v.get("suggested_model"):
            print(f"           send to '{v['suggested_model']}' "
                  f"(lane: {v.get('required_lane')})")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Dry-run a payload against the PHI guardrail.")
    ap.add_argument("text", nargs="*", help="text to check")
    ap.add_argument("--file", "-f", action="append", default=[], help="file(s) to check")
    ap.add_argument("--model", "-m", default="", help="target model, if known")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fail-on-block", action="store_true",
                    help="exit 1 if any payload would be blocked (for CI)")
    args = ap.parse_args()

    if not API_KEY:
        print("PHI_API_KEY is not set", file=sys.stderr)
        return 2

    items: list[tuple[str, str]] = []
    for path in args.file:
        try:
            items.append((path, open(path).read()))
        except OSError as exc:
            print(f"cannot read {path}: {exc}", file=sys.stderr)
            return 2
    if args.text:
        items.append(("<argument>", " ".join(args.text)))
    if not items and not sys.stdin.isatty():
        items.append(("<stdin>", sys.stdin.read()))
    if not items:
        ap.print_help()
        return 2

    blocked = False
    results = []
    for label, text in items:
        try:
            v = phi_check(text, args.model)
        except Exception as exc:  # noqa: BLE001
            print(f"gateway unreachable at {GATEWAY}: {exc}", file=sys.stderr)
            return 2
        blocked |= not v["allowed"]
        results.append({"source": label, **v})
        if not args.json:
            _render(label, v)

    if args.json:
        print(json.dumps(results, indent=2))

    return 1 if (blocked and args.fail_on_block) else 0


if __name__ == "__main__":
    sys.exit(main())
