#!/usr/bin/env python3
"""
Demo harness. Five requests, run in this order, in front of the room.

The narrative arc matters more than the code:
  1 & 2 establish that both lanes work.
  3 is the denial.
  4 is the denial that survives a caller lying about the data class.
  5 shows policy choosing the lane when the caller does not.

Usage:
    export GATEWAY=http://<gateway-ip>:4000
    export LITELLM_MASTER_KEY=sk-poc-changeme
    python scenarios.py            # all five
    python scenarios.py 3          # just scenario 3
"""

import json
import os
import sys

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

GATEWAY = os.environ.get("GATEWAY", "http://localhost:4000")
KEY = os.environ.get("LITELLM_MASTER_KEY", "sk-poc-changeme")

console = Console()

# --- synthetic records. Synthea-shaped. NEVER use real PHI. ----------------
PHI_NOTE = (
    "Patient Marcus Delgado, MRN 4471822, DOB 1968-03-14, reachable at "
    "919-555-0177. Presented with exertional dyspnea over three weeks. "
    "Echo shows EF 38%. Summarize the assessment for the care team."
)

CLEAN_NOTE = (
    "Summarize the current first-line pharmacologic management of heart "
    "failure with reduced ejection fraction. Cite drug classes only, no "
    "patient specifics."
)

SCENARIOS = [
    {
        "n": 1,
        "name": "Non-PHI to the frontier lane",
        "expect": "ALLOW — no identifiers, external model permitted",
        "model": "frontier-claude",
        "data_class": "non-phi",
        "content": CLEAN_NOTE,
    },
    {
        "n": 2,
        "name": "PHI to the in-tenancy lane",
        "expect": "ALLOW — identifiers present, but lane permits PHI",
        "model": "intenancy-llama",
        "data_class": "phi",
        "content": PHI_NOTE,
    },
    {
        "n": 3,
        "name": "PHI to the frontier lane",
        "expect": "DENY — PHI_LANE_VIOLATION, fails closed",
        "model": "frontier-claude",
        "data_class": "phi",
        "content": PHI_NOTE,
    },
    {
        "n": 4,
        "name": "PHI mislabelled as non-PHI by the caller",
        "expect": "DENY — caller claim overridden by independent classification",
        "model": "frontier-claude",
        "data_class": "non-phi",
        "content": PHI_NOTE,
    },
    {
        "n": 5,
        "name": "Caller delegates the choice (model=auto)",
        "expect": "ALLOW — policy selects the frontier lane for clean text",
        "model": "auto",
        "data_class": "unspecified",
        "content": CLEAN_NOTE,
    },
]


def run(sc: dict) -> None:
    header = f"[bold]Scenario {sc['n']} — {sc['name']}[/bold]"
    console.print(Panel(header, style="cyan", expand=False))

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("[dim]requested model[/dim]", sc["model"])
    t.add_row("[dim]x-data-class[/dim]", sc["data_class"])
    t.add_row("[dim]expected[/dim]", sc["expect"])
    console.print(t)

    try:
        r = httpx.post(
            f"{GATEWAY}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {KEY}",
                "x-data-class": sc["data_class"],
                "Content-Type": "application/json",
            },
            json={
                "model": sc["model"],
                "messages": [{"role": "user", "content": sc["content"]}],
                "max_tokens": 150,
            },
            timeout=120,
        )
    except httpx.RequestError as exc:
        console.print(f"[red]transport error: {exc}[/red]\n")
        return

    if r.status_code == 200:
        body = r.json()
        served = body.get("model", "?")
        text = body["choices"][0]["message"]["content"].strip()
        console.print(f"[green]200 ALLOW[/green]  served by: [bold]{served}[/bold]")
        console.print(f"[dim]{text[:220]}{'...' if len(text) > 220 else ''}[/dim]\n")
    elif r.status_code == 403:
        detail = r.json().get("error", {})
        if isinstance(detail, dict):
            detail = detail.get("message", detail)
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except (ValueError, TypeError):
                pass
        console.print("[red]403 POLICY_DENY[/red]")
        console.print(
            Panel(
                json.dumps(detail, indent=2) if isinstance(detail, dict) else str(detail),
                border_style="red",
                expand=False,
            )
        )
        console.print()
    else:
        console.print(f"[yellow]{r.status_code}[/yellow] {r.text[:400]}\n")


def main() -> None:
    console.print(f"\n[bold]Gateway:[/bold] {GATEWAY}\n")
    picks = SCENARIOS
    if len(sys.argv) > 1:
        wanted = {int(a) for a in sys.argv[1:]}
        picks = [s for s in SCENARIOS if s["n"] in wanted]
    for sc in picks:
        run(sc)


if __name__ == "__main__":
    main()
