#!/usr/bin/env python3
"""
Turn the audit log into the compliance answer.

This is the 'evidence, not archaeology' claim made concrete: the question
"did any PHI reach a non-tenancy model last quarter?" becomes one query.
"""
import json
import sys
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.table import Table

path = Path(sys.argv[1] if len(sys.argv) > 1 else "../gateway/audit/audit.jsonl")
rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
decisions = [r for r in rows if r.get("decision") in {"allow", "deny", "reroute"}]

c = Console()
c.print(f"\n[bold]Audit records:[/bold] {len(rows)}  "
        f"([dim]{len(decisions)} policy decisions[/dim])\n")

t = Table(title="Policy decisions")
for col in ("time", "caller claim", "detected PHI", "requested", "lane", "decision", "reason"):
    t.add_column(col, overflow="fold")
for r in decisions:
    style = "red" if r["decision"] == "deny" else "green"
    t.add_row(
        r.get("ts", "")[11:19],
        r.get("claimed_data_class", ""),
        "yes" if r.get("detected_phi") else "no",
        r.get("requested_model", ""),
        r.get("resolved_lane") or "-",
        f"[{style}]{r['decision']}[/{style}]",
        r.get("reason_code", ""),
    )
c.print(t)

# --- the question a privacy office actually asks --------------------------
leaks = [
    r for r in decisions
    if r.get("detected_phi") and r.get("decision") in {"allow", "reroute"}
    and r.get("resolved_lane") != "in_tenancy"
]
overrides = [r for r in decisions if r.get("claim_overridden")]

c.print()
c.print(f"[bold]PHI executed outside the tenancy:[/bold] "
        f"{'[red]' + str(len(leaks)) + ' — INVESTIGATE[/red]' if leaks else '[green]0[/green]'}")
c.print(f"[bold]Caller data-class claims overridden:[/bold] {len(overrides)}")
c.print(f"[bold]Reason codes:[/bold] "
        f"{dict(Counter(r.get('reason_code') for r in decisions))}\n")
