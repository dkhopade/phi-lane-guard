# Findings

Things discovered by building and testing rather than by reading documentation.
Every claim here was verified against a running system; the version is stated
because behaviour has varied across releases.

| File | What it covers |
|---|---|
| `control-plane.md` | Whether LiteLLM's database ends up holding regulated content, and what controls that |
| `integration-design.md` | Proposal for composing this guard with a platform's own intelligent router |
| `upstream-issue-draft.md` | Draft bug report for the column mismatch |

## The pattern across all of them

Six independent failures found in this project, one shape:

1. **A PII classifier scoring below its threshold** — Presidio's phone
   recognizer returns a flat 0.4 for US numbers, below the 0.5 default. Phone
   numbers were silently undetected. No error, no warning.
2. **`firewall-cmd` hanging rather than failing** — cloud-init runs before
   firewalld answers on D-Bus, so the call blocks forever and `|| true` catches
   a bad exit code, not a hang.
3. **A teardown script exiting before deleting anything** — an invalid
   `--wait-for-state` value meant the script errored out while a GPU kept
   billing.
4. **A compliance query reading the wrong column** — enabled prompt storage
   writes to `proxy_server_request`, not the documented `messages`, so the
   obvious verification returns clean while the content sits two columns over.
5. **A guardrail test panel reporting a false pass** — the console calls an
   interface the base class implements as a no-op, so it reports success while
   the API denies the same content.
6. **A hook that silently stops being called** — when a guardrail implements
   both `apply_guardrail()` and `async_pre_call_hook()`, LiteLLM calls the
   former and the latter never runs. Enforcement still worked, but the richer
   denial body built in the hook silently disappeared from responses.

**The dangerous failure is the silent one.** A crash tells you. A hang, a
default that returns nothing, or a check that reads the wrong field does not —
and in every case above, the failure looked like success from the outside.

This is the argument for testing controls through the path they will actually
be exercised on, rather than the path that is convenient to click.
