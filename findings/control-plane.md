# Finding: the control plane is in scope for regulated data

**Tested on:** LiteLLM 1.98.0, image `docker.litellm.ai/berriai/litellm-database:latest`,
digest `sha256:653fd100d630`, Postgres 16, quickstart compose, single worker.
**Method:** empirical. Every claim below was verified against the database, not
read from documentation.

---

## Summary

The platform's Postgres holds keys, teams, spend and model configuration. It can
also hold **request and response content**, including anything a caller sends.

The default is safe. But the setting that changes it is a console toggle
requiring no restart, and once enabled the content lands in a column other than
the one the documentation names. **A compliance check written the obvious way
returns a false negative.**

Recommendation: **treat the control-plane database as in scope for regulated
data regardless of configuration**, and audit it column-agnostically.

---

## What was tested, and what happened

### 1. Default: no content persisted

A request carrying synthetic patient identifiers (name, MRN, DOB, phone) was
sent through the gateway. All four candidate JSON columns in
`LiteLLM_SpendLogs` were empty:

```
messages = {}    response = {}    proxy_server_request = {}    metadata = {}
```

Token counts, cost, model, latency and caller IP were recorded. Content was not.
**The out-of-the-box posture is good.**

### 2. One console toggle changes it

`store_prompts_in_spend_logs` is exposed in the Admin UI under Admin Settings.
Enabling and saving it takes effect immediately — no restart, no deployment, no
config file change. It is also settable in `general_settings`, where it does
require a restart; the UI path overrides config.

**Implication:** in a deployment where config is reviewed and approved, the
approved config is not the operative control. Whoever holds proxy-admin rights
on the console can enable content persistence without producing a diff, a
deployment, or a change-control record.

**The control is RBAC on the Admin UI, not config review.** That belongs in the
platform's tenancy design.

### 3. Content lands in the wrong column

After enabling the toggle, the same request was repeated. Result:

| Column | Contains the identifier |
|---|---|
| `messages` | **no** |
| `response` | **yes** |
| `proxy_server_request` | **yes** |
| `metadata` | no |

The prompt is stored inside `proxy_server_request`, nested under a `messages`
key within that JSON. The column literally named `messages` stays `{}`.

The stored record is also richer than the prompt alone — it includes request
headers, caller user-agent, and the endpoint:

```json
{"model": "...", "messages": [{"role": "user", "content": "<full prompt>"}],
 "metadata": {"headers": {"host": "...", "user-agent": "curl/8.7.1", ...},
              "endpoint": "..."}}
```

**Implication:** the natural verification query —

```sql
SELECT messages FROM "LiteLLM_SpendLogs";   -- returns {}
```

— reports no content stored while the content is two columns away. Any audit,
DLP scan or evidence-gathering exercise targeting the documented column produces
a false negative.

The Admin UI displays the content correctly, so this is a database-layer
mismatch rather than a UI defect. Upstream issue #23636 reports the same column
placement; the UI symptom described there does not reproduce on 1.98.0.

### 4. Feature history is inconsistent across versions

Upstream issues #15641 (v1.77.2) and #34747 (v1.93.0) report this setting
enabled with **no** content persisted at all. On 1.98.0 content *is* persisted,
in the columns described above.

**Implication:** behaviour varies by release in both directions. Neither "it
stores content" nor "it doesn't" is safe to assume. **Verify on the pinned
version**, and pin one.

---

## What this means for the platform

**The control plane sits inside the compliance boundary, not outside it.** Not
because it always holds regulated data, but because it can be made to with a
console click, and because the standard way of checking says otherwise.

Concretely, for a regulated tenancy:

| Concern | What it requires |
|---|---|
| Where Postgres runs | Inside the customer's compliance boundary, not a shared control plane |
| Who can enable content storage | Proxy-admin rights, tightly scoped and audited |
| Retention | Set deliberately — there is a retention-period setting alongside the toggle |
| Verification queries | Column-agnostic, never targeting `messages` alone |
| Spend logging | `disable_spend_logs: true` is an option where spend tracking is not needed per tenant |
| Version | Pinned, with this behaviour re-verified on that pin |

**And it sharpens the hosting question.** Customer-self-hosted: this is their
database, their admin, their boundary. Oracle-hosted: an Oracle-operated
database can come to hold customer PHI after a console toggle by whoever holds
admin — which is a BAA conversation, made concrete rather than hypothetical.

---

## Related finding: Redis and enforcement

Separately, the Admin UI warns when no Redis is configured. Without it, rate
limits, budgets, cooldowns and cache invalidation are per-worker: a limit of 100
requests/minute across four workers admits 400, and **a revoked key stays usable
on workers that did not process the revocation**. Multi-pod deployments without
Redis are documented as beta, with the list of degraded functionality explicitly
non-exhaustive.

Relevant because the platform's stated goals include token economics and
intelligent routing, both of which depend on that shared state — and because a
key revoked for a compliance reason not taking effect uniformly is the kind of
detail a customer's security team will ask about.

Setting `REDIS_HOST` and `REDIS_PORT` in the environment is not sufficient on
its own; the proxy reads them only when the config points at Redis, and both
`router_settings` and the cache block need configuring.

---

## The pattern

Four findings across this project, one shape:

1. A PII classifier scoring below its threshold, detecting nothing, logging nothing.
2. `firewall-cmd` hanging rather than failing, so `|| true` did not protect it.
3. A teardown script exiting on an argument error before deleting anything.
4. A compliance query reading the wrong column and returning clean.

**The dangerous failure is the silent one.** Each of these looks like success
from the outside. It is the argument for testing controls rather than
configuring them and assuming — and it is worth stating explicitly in the
platform's design principles.
