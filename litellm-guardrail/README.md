# PHI Lane Guard as a native LiteLLM guardrail

The PoC in the repo root proves the control with a sealed OCI subnet. This
directory is the same control implemented the way a LiteLLM-based platform would
actually consume it — as a registered `CustomGuardrail` rather than a bolted-on
callback.

## Why a guardrail rather than a callback

| | `CustomLogger` (the PoC) | `CustomGuardrail` (here) |
|---|---|---|
| Scope | Global, all traffic | Configurable per key and per team |
| Decision logging | Your own audit file | Recorded natively in `LiteLLM_SpendLogs` |
| Invocation | Always | `default_on`, or named per request |

For a multi-tenant platform the middle row matters most: LiteLLM records which
guardrail fired, its status and duration, without any code from you.

## Run it

Against a local LiteLLM (gateway + Postgres via docker compose):

```bash
docker compose build litellm && docker compose up -d
./test-guardrail.sh
```

Expected: clean text allowed; PHI denied with `PHI_LANE_VIOLATION`, including
when the caller labels it `non-phi` and when no label is sent at all.

## For developers

The point of a control like this is not that it blocks you — it's that you stop
having to know whether a given payload contains identifiers. `phi_check.py`
lets you ask before sending, without calling a model or spending tokens:

```bash
export PHI_GATEWAY=http://localhost:4000
export PHI_API_KEY=sk-...

./phi_check.py "Patient Marcus Delgado, MRN 4471822, call 919-555-0177."
#   BLOCKED  PHI_LANE_VIOLATION · detected: MRN, PERSON, PHONE_NUMBER
#            send to 'intenancy-llama' (lane: in-tenancy)
```

It uses LiteLLM's existing `POST /guardrails/apply_guardrail`, so there is no
custom route to maintain and it survives platform upgrades.

`--fail-on-block` returns exit 1, which makes the boundary assertable in CI:

```bash
./phi_check.py --file fixtures/*.txt --fail-on-block
```

```python
assert phi_check(clinical_note)["allowed"] is False
```

Denials are actionable rather than merely correct — they carry
`suggested_model`, `required_lane` and `how_to_fix`, so the response tells a
developer where to send the request instead of only that they were wrong.

## Two things that are not obvious

**`apply_guardrail()` takes precedence over `async_pre_call_hook()`.** When a
guardrail implements both, LiteLLM 1.98.0 calls `apply_guardrail()` and the
hook never runs — verified from the traceback. So `apply_guardrail()` carries
the full decision here, and the hook is kept as a fallback for versions that
call it instead.

**But implement both anyway.** The inverse is the real trap: the base class
implements `apply_guardrail()` as `return inputs` — a no-op. A guardrail with
only the hook blocks correctly in production while **the Admin UI's own
guardrail test panel reports success in ~24 microseconds**. The tool for
verifying the control gives a false pass.

**Mount the code during development.** Baking `phi_guardrail.py` into the image
means a rebuild per edit, and Docker layer caching can silently serve a stale
copy. Mount it as a volume while iterating; bake it for deployment.

## Verified behaviour (LiteLLM 1.98.0)

- Config-defined guardrails **cannot be disabled from the Admin UI**
- A same-named guardrail created in the UI **does not shadow** the config one
- `policy.yaml` is invisible to the console — it stays a version-controlled
  compliance artifact

That makes config the right home for a compliance control. Note this is
specific to guardrails: `store_prompts_in_spend_logs` behaves the opposite way
and can be set in the UI to override config. See `../findings/control-plane.md`.

## Files

| File | Purpose |
|---|---|
| `guardrails/phi_guardrail.py` | The guardrail. Both entry points. |
| `guardrails/policy.yaml` | Which models may see PHI, and what counts as PHI |
| `guardrails/Dockerfile` | Extends the stock image with Presidio + spaCy |
| `litellm_config.yaml` | Registers the guardrail with the proxy |
| `test-guardrail.sh` | Four cases against a local proxy |
| `phi_check.py` | Developer dry-run client. Human, JSON and CI modes. |

Deploying to Kubernetes instead? See [`../oke/`](../oke/).
