# PHI Lane Guard — integration design for the OCI LiteLLM platform

**Status:** proposal · **Author:** Deepak Khopade · **Audience:** OCI AI Guild, LiteLLM platform team

---

## Summary

The platform's intelligent router decides *which* model, service, or compute
serves a request, weighing cost, latency, quality, and compute class. This
proposal adds one thing it cannot safely do on its own: **decide what the data
is, and constrain where it may execute, without trusting the caller.**

The integration is deliberately narrow. PHI Lane Guard does not route. It
classifies request content server-side and emits routing **tags** that the
platform's existing tag-filtering layer already understands. The router then
optimizes freely among whatever deployments remain eligible.

```
  caller ──► [ classify content ] ──► tags ──► [ platform router ] ──► deployment
             PHI Lane Guard                     cost · latency · quality
             (this proposal)                    (already yours)
```

One sentence version: **the router optimizes, the guard constrains, and a
constraint the caller can set is not a constraint.**

---

## 1. Why this is needed — the gap in tag-based routing

LiteLLM supports tag-based routing: deployments carry tags, requests carry tags
in metadata, and the router returns only deployments whose tags match. Enabled
with `enable_tag_filtering` in `router_settings`. This is the correct mechanism
for residency, and the platform should use it.

The gap is where tags come from. In open-source LiteLLM today, **tags are
caller-supplied** — passed in the request body or the `x-litellm-tags` header.
The `reject_clientside_metadata_tags` setting blocks client-supplied tags but
does not enforce which tags a key *must* carry; the team-based enforcement that
would is an Enterprise feature. There is an open upstream issue tracking exactly
this (BerriAI/litellm#22966), which describes the consequence plainly: a caller
can pass any tag, or none, and bypass an intended regional restriction.

For cost tiers, caller-supplied tags are fine. For **residency they are not**,
for two reasons, and the second matters more than the first:

1. A malicious caller can assert its way into a lane it should not reach.
2. **A well-intentioned developer can simply be wrong.** They do not always know
   whether a given payload contains patient identifiers — free-text clinical
   notes are the normal case, not the edge case.

The second is the realistic failure mode in an enterprise, and no amount of
policy documentation fixes it. The tag has to be derived from the content.

---

## 2. Proposed layering

| Layer | Owner | Responsibility |
|---|---|---|
| Classification | PHI Lane Guard | Inspect request content in-process; determine data class |
| Constraint | PHI Lane Guard | Emit required tags; deny when no compliant lane exists |
| Filtering | Platform | `enable_tag_filtering` narrows eligible deployments |
| Optimization | Platform | Cost, latency, quality, compute class among survivors |
| Execution | Platform | OCI-native, self-hosted GPU/CPU, interconnect, partner clouds |
| Attribution | Platform | Token economics and chargeback, keyed by the same tags |

The guard writes tags. It never sets `model`, never picks a deployment, and
never overrides a routing decision. Everything the platform already does
continues to work unchanged.

---

## 3. The tag contract

Tags are the entire interface between the two components. Proposed namespaces:

| Namespace | Values | Meaning |
|---|---|---|
| `residency:` | `in-tenancy`, `in-region`, `unrestricted` | Where inference may physically execute |
| `dataclass:` | `phi`, `deidentified`, `public` | What the classifier determined the content to be |
| `compute:` | `gpu`, `cpu`, `any` | Compute class the policy permits or prefers |

A PHI request emits:

```
residency:in-tenancy   dataclass:phi   compute:gpu
```

A clean request emits:

```
residency:unrestricted   dataclass:public   compute:any
```

Deployments in the platform's `model_list` carry the tags they satisfy. A
self-hosted vLLM deployment inside the customer VCN carries
`residency:in-tenancy`; an external partner model carries
`residency:unrestricted`. **The platform decides what to run; the tags decide
what is eligible.**

Two properties worth noting:

- The namespaces are extensible without touching the guard. Export control
  (`control:itar`), tenant isolation (`tenant:acme`), and sovereignty
  (`residency:eu-only`) are additional values, not new code.
- Because LiteLLM uses tags for spend tracking as well as routing, the platform
  gets **cost attribution by data class for free** — spend on PHI workloads
  versus general workloads becomes a query, with no parallel ledger.

---

## 4. Policy as a reviewable artifact

The mapping from data class to required tags lives in a YAML file, not in code.
This is a deliberate choice: a privacy officer who cannot read Python can read,
diff, and approve it, and it can be version-controlled as a compliance artifact.

```yaml
classifications:
  phi:
    description: >-
      Content containing patient identifiers. Must execute inside the
      customer tenancy with no egress path.
    required_tags: [residency:in-tenancy, dataclass:phi]
    on_no_eligible_deployment: deny

  deidentified:
    description: >-
      Content processed by an approved de-identification step.
    required_tags: [dataclass:deidentified]
    on_no_eligible_deployment: deny

  public:
    description: No identifiers detected. Free to route on quality and cost.
    required_tags: [residency:unrestricted, dataclass:public]
    on_no_eligible_deployment: deny
```

The platform never edits this file, and the guard never edits the platform's
routing configuration. Clean ownership boundary in both directions.

---

## 5. Configuration the platform must set

Three settings, all on the platform side. The first two are load-bearing — the
guard is not an enforcement point without them.

```yaml
router_settings:
  enable_tag_filtering: true

general_settings:
  # Callers must not be able to set routing tags themselves. Without this,
  # a caller can assert residency:unrestricted and bypass classification
  # entirely — the guard becomes advisory rather than enforcing.
  reject_clientside_metadata_tags: true
```

1. **`enable_tag_filtering: true`** — without it, emitted tags are inert.
2. **`reject_clientside_metadata_tags: true`** — without it, the guard is
   bypassable and provides no assurance worth claiming to a customer.
3. **Hook ordering** — the guard must run before tag filtering. This needs to be
   confirmed as a guarantee in the platform's pipeline, not assumed.

**Recommendation:** treat items 1 and 2 as a platform invariant, not a per-tenant
toggle. A deployment with tag filtering on and client tags accepted looks
compliant and is not, which is the most dangerous of the three possible states.

---

## 6. Deny semantics

Tag filtering that matches nothing produces a router error. That is not good
enough for a regulated customer, because *"no deployment available"* and
*"policy forbids this"* are different facts with different remediations.

The guard fails closed with an explicit reason code before the router is
reached:

```json
{
  "error": "POLICY_DENY",
  "reason_code": "NO_COMPLIANT_LANE",
  "detected_dataclass": "phi",
  "required_tags": ["residency:in-tenancy"],
  "caller_asserted": "public",
  "claim_overridden": true,
  "request_id": "..."
}
```

`claim_overridden` is the field that matters in a customer conversation. It
records that the caller said one thing, the platform determined another, and the
platform's determination won.

---

## 7. Audit and token economics

The guard writes one record per decision — allow and deny alike — carrying the
request ID, caller identity, detected class, emitted tags, and reason code.

It does **not** track spend. The platform already attributes cost per key, team,
and tag; the guard's record correlates to that by request ID. Two systems, one
join key, no duplicated ledger.

This is what turns *"did any PHI reach a non-tenancy model last quarter?"* from a
log investigation into a query — the compliance question the platform is
otherwise unable to answer.

---

## 8. Resilience to the Rust gateway question

LiteLLM has published a Rust implementation of the gateway with substantially
lower overhead. **Whether the platform targets it changes how the classifier is
hosted, but not this design.**

The tag contract in section 3 is language-agnostic. Two implementation paths
satisfy it identically:

| Path | How the classifier runs | When to choose it |
|---|---|---|
| **In-process hook** | Python pre-call hook inside the gateway | Python gateway; lowest latency; what the PoC uses today |
| **External guardrail service** | HTTP service the gateway calls pre-request | Rust gateway, or when the classifier must scale or be versioned independently |

The external-service path is how commercial guardrail providers already
integrate with LiteLLM, so it is a supported shape rather than a workaround. It
costs a network hop and gains independent scaling and deployment.

**This does not need to be decided now.** Agreeing the tag contract first means
the hosting decision can follow the platform's own roadmap.

---

## 9. What this proposal does not ask for

Scope boundaries, stated up front:

- **No changes to the routing strategy.** Cost, latency, and quality
  optimization remain entirely the platform's.
- **No new gateway.** This is a component inside the platform's gateway, not a
  second one in front of it.
- **No claim of certification.** The guard enforces where inference runs and
  records what happened. BAA coverage, clinical validation, and the
  de-identification method itself remain the customer's responsibility. This
  boundary should be stated to customers rather than blurred.
- **No dependency on Enterprise licensing.** The design uses open-source tag
  filtering. Enterprise team-based tag enforcement would strengthen it, but is
  not required for it to work.

---

## 10. Open questions for the platform team

Ordered by how much they would change the design:

1. **Is the platform targeting the Python gateway or the Rust gateway?**
   Determines in-process hook versus external guardrail service (section 8).
2. **Is `reject_clientside_metadata_tags` set, and is it a platform invariant
   or a per-tenant setting?** Determines whether the guard is enforcing or
   advisory (section 5).
3. **Is guardrail execution guaranteed to precede tag filtering?**
4. **Where does policy live — central, or per tenancy?** A single policy across
   all customers is simpler; per-tenant policy needs a scoped policy store.
   Product decision, not a technical one.
5. **Which tag namespaces already exist?** If the platform has conventions for
   cost tier or compute class, the residency namespaces should be consistent
   with them rather than parallel.

---

## 11. Working reference

A functioning proof of concept exists and was verified end to end on OCI:
two lanes, a sealed private subnet with no internet route, five test scenarios
including two denials, and an audit trail showing zero PHI executed outside the
tenancy. Classification overhead measured at roughly 7 ms.

It currently overrides the model directly rather than emitting tags — this
proposal is the refactor that makes it a platform component instead of a
standalone gateway.

Code and write-up: `github.com/dkhopade/phi-lane-guard`

The PoC also surfaced defects worth carrying into any production design, all of
which share one shape — **the dangerous failure is the silent one**:

- Presidio's phone recognizer returns a flat 0.4 confidence for US numbers,
  below a 0.5 threshold. Phone numbers were silently undetected. No error, no
  warning.
- An SSN matched as `PHONE_NUMBER`. Routing was correct; the audit record named
  the wrong identifier class — which matters when the record *is* the
  compliance artifact.
- Site-specific identifiers (MRN formats, trial subject IDs, accession numbers)
  are invisible until someone writes a recognizer. Universal identifiers work
  out of the box; customer formats are a scoping conversation.

A shared recognizer library and a test harness that asserts *"zero PHI outside
the tenancy"* in CI would be worth more to the field than either of us building
them separately.
