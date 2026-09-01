# PHI Lane Guard on OKE — handoff

For running this guardrail inside an existing LiteLLM deployment on OKE. Written
for whoever operates that cluster, not for the person who wrote the guardrail.

**What it does:** classifies request content in-process before anything leaves
the proxy, and blocks PHI-bearing requests aimed at models not permitted to
process PHI. Detection is a local NER pass — never an LLM call, because sending
text to a model to ask whether it contains PHI means the PHI has already left.

**Measured overhead:** under 6 ms steady state on the Python gateway.

---

## The one constraint that shapes everything

The classifier is Presidio plus a spaCy language model, roughly 590 MB. That
**cannot** be delivered as a ConfigMap. It has to be in the container image.

So the split is:

| Piece | How it ships | Why |
|---|---|---|
| Presidio + spaCy model | **baked into the image** | Too large for a ConfigMap; also means the guardrail has no network dependency and cannot fail open because a detection service was unreachable |
| `phi_guardrail.py` | baked into the image | Changes rarely; belongs under version control with the image tag |
| `policy.yaml` | **ConfigMap** | The compliance artifact. Tuned per tenant, reviewable, changeable without rebuilding |
| Guardrail registration | your existing proxy config | One block added to what you already have |

---

> Publishing this to OCIR and granting pull access?
> See [`PUBLISHING-TO-OCIR.md`](PUBLISHING-TO-OCIR.md).

## Step 1 — Build and push the image

The image extends whatever LiteLLM version you currently run. **Match your
running tag**, don't assume `latest`:

```bash
kubectl get deploy <your-litellm-deploy> -o jsonpath='{.spec.template.spec.containers[0].image}'
```

Put that tag in the Dockerfile's `FROM`, then:

```bash
export OCIR=<region>.ocir.io/<tenancy-namespace>/phi-lane-guard
docker build -t $OCIR:1.0.0 .
docker push $OCIR:1.0.0
```

Two things that bite here:

- **The stock LiteLLM image ships without `pip`.** Its venv was built with `uv`
  and stripped, so the Dockerfile bootstraps pip via `ensurepip`. Don't remove
  that line.
- **The image runs Python 3.13**, so pip resolves spaCy 3.8.x rather than 3.7.x.
  The Dockerfile uses `spacy download` rather than a pinned model URL for
  exactly this reason — a pinned URL fails on version mismatch.

The build needs internet egress to PyPI and to spaCy's model host on GitHub. If
your build runners are air-gapped, mirror those first.

## Step 2 — Create the policy ConfigMap

```bash
kubectl -n <namespace> create configmap phi-lane-policy \
  --from-file=policy.yaml=k8s/policy.yaml
```

Edit `policy.yaml` before applying. The field that matters:

```yaml
lanes:
  phi_permitted_models:
    - <your in-tenancy model names, exactly as they appear in model_list>
```

**This is an allowlist and it is deny-by-default.** An empty list denies PHI to
everything. Model names must match what callers actually request — if they
don't, PHI is denied everywhere, which is the safe direction but confusing.

## Step 3 — Patch the deployment

Add the image, the ConfigMap mount, and `PYTHONPATH` so the proxy can import
the module. `k8s/deployment-patch.yaml` has a strategic-merge patch;
`k8s/helm-values.yaml` has the equivalent if you deploy via the chart.

```bash
kubectl -n <namespace> patch deploy <your-litellm-deploy> \
  --patch-file k8s/deployment-patch.yaml
```

Note `PYTHONPATH=/app/guardrails` — without it the proxy cannot resolve
`phi_guardrail.PHILaneGuard` and the guardrail silently fails to register.

## Step 4 — Register the guardrail in your proxy config

Add to your existing config — wherever it lives, ConfigMap or Helm values:

```yaml
guardrails:
  - guardrail_name: "phi-lane-guard"
    litellm_params:
      guardrail: phi_guardrail.PHILaneGuard
      mode: "pre_call"
      default_on: true
```

**Keep this in config rather than creating it through the Admin UI.** Verified
behaviour: a config-defined guardrail cannot be disabled from the console, and
a same-named guardrail created in the UI does not shadow it. That makes config
the right home for a compliance control — changes require a deployment and
produce a reviewable diff.

Note this is *not* a general property of LiteLLM config. `store_prompts_in_spend_logs`,
for instance, can be set in the UI and overrides config. Guardrails are the
exception, in the direction you want.

## Step 5 — Verify

```bash
kubectl -n <namespace> rollout status deploy/<your-litellm-deploy>

# the guardrail is registered
curl -s https://<your-gateway>/guardrails/list \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq '.guardrails[] | {guardrail_name, guardrail_definition_location}'

# it blocks
./test-guardrail.sh https://<your-gateway> $LITELLM_MASTER_KEY <a-model-name>
```

Expected: clean text allowed, PHI-bearing text denied with
`PHI_LANE_VIOLATION`, including when the caller labels it `non-phi`.

---

## Things worth knowing before you deploy

**The guardrail implements two interfaces, deliberately.** LiteLLM invokes
guardrails through `async_pre_call_hook()` on the real request path and
`apply_guardrail()` from the Admin UI's guardrail test panel. The base class
implements the latter as a no-op — so a guardrail with only the hook blocks
correctly in production while **the console's own test panel reports success**.
Both are implemented here. If you fork this, keep both.

**Presidio detects a subset of identifiers, and site-specific formats need
work.** Universal identifiers (SSN, email, phone) work out of the box. MRN has
a hand-written recognizer here. Accession numbers, trial subject IDs and your
own patient-ID formats are invisible until someone writes a pattern —
`Subject 0042` passes undetected today. Plan a scoping pass against your real
data formats.

**It over-denies rather than under-denies, on purpose.** A hospital staff phone
number classifies as PHI. So does a bare clinician name. For a control this is
the correct direction, but production needs context rules, an allow-list and a
documented exception path.

**Not HIPAA Safe Harbor.** Safe Harbor enumerates 18 identifier classes; this
covers a subset. Imagery, biometrics and device identifiers are out of scope.

**Detection thresholds are tuned for a reason.** Presidio's phone recognizer
returns a flat 0.4 confidence for US numbers, below the 0.5 default floor —
phone numbers were silently undetected until the classifier was tested in
isolation. The per-entity override in `policy.yaml` fixes it. Don't remove it,
and if you change thresholds, test the classifier separately from the pipeline.

**Audit is split between two systems.** LiteLLM natively records which
guardrail fired, its status and duration, in `LiteLLM_SpendLogs.metadata ->
guardrail_information`. It redacts the guardrail's response by design, so the
reason code and detected entity types are **not** persisted by the platform.
If you need those for compliance evidence, add a logging callback that captures
them and joins on `request_id`.

---

## Control-plane note, worth raising with whoever owns compliance

Prompt and response content is **not** stored by default. But
`store_prompts_in_spend_logs` can be enabled from the Admin UI with no restart,
and the UI setting overrides config. When enabled, content lands in
`proxy_server_request` and `response` — **not** the `messages` column the
documentation points at.

Consequences for an OKE deployment:

- A compliance check querying `messages` returns clean while content sits two
  columns over. Any verification must be column-agnostic.
- The operative control is proxy-admin RBAC, not config review.
- The control-plane Postgres should be treated as in scope for regulated data
  regardless of the current setting, because the setting is one console click
  away.

Verified on LiteLLM 1.98.0. Behaviour has varied across releases in both
directions — verify on whatever version you pin.

---

## What I need from you to help further

- How LiteLLM is deployed — the official Helm chart, or your own manifests?
- The image tag currently running.
- Where the proxy config lives — ConfigMap, Helm values, or DB?
- Is Redis configured? Without it, budgets and rate limits are per-replica, so
  a limit of 100/min across 4 replicas admits 400, and a revoked key stays
  usable on replicas that haven't seen the revocation.
- Are your in-tenancy models actually network-isolated? The guardrail enforces
  *which model* may receive PHI. Whether that model can reach the internet is a
  separate control, and both are needed for the residency claim to hold.
