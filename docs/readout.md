# Readout — 10 minutes

Structure this as *problem, failed request, route table, audit line, limits*.
Resist the urge to walk the architecture first; lead with the failure.

---

## 1. The problem (60 seconds, no slides)

Every regulated customer stalls at the same place. Not model quality — the
question "where did the PHI go, and can you prove it?" Today the answer lives in
application code: each developer decides whether their request carries PHI and
which provider is allowed. That judgment is distributed, undocumented and it
drifts. Compliance can't audit it without reading every repo.

## 2. The failed request (2 minutes — this is the demo)

Run scenario 3, then scenario 4. Say while it runs:

> "Same prompt both times. The second one, the developer has labelled it
> non-PHI — either carelessly or because they genuinely believed it. The
> gateway doesn't take their word for it. It classifies independently, locally,
> before anything leaves the process, and it fails closed."

Point at `claim_overridden: true`. That field is the argument.

## 3. The route table (90 seconds)

Console, private subnet, route rules: empty. Then the curl from the model host
timing out. Then scenario 2 succeeding.

> "The model host cannot reach the internet. Not by policy — by topology. And
> inference still works, because the gateway reaches it over the VCN. This is
> the part that isn't code, and it's the part a security reviewer will actually
> accept."

## 4. The audit line (60 seconds)

`python audit_report.py`, then point at one line:

> "PHI executed outside the tenancy: zero. That's the question the privacy
> office asks every quarter. Right now, answering it means archaeology across
> service logs. Here it's a query."

## 5. The limits (90 seconds — do not skip)

Read from the *Honest limitations* section. Especially:

- not Safe Harbor, detects a subset of identifiers
- de-ID lane is a stub
- over-denies: a staff phone number trips it, and that's the safe direction
- found a silent detection bug (phone confidence below threshold) only by
  testing the classifier separately

> "I'd rather show you where this breaks than let you find it later. The
> over-denial is deliberate — for a control, failing closed is correct — but a
> production version needs context rules and a documented exception path."

## 6. What it generalizes to (60 seconds)

Not a healthcare feature. Data residency, export control, sovereign cloud, and
per-tenant isolation are the same shape: classify at the door, enforce with
topology, record the decision. Same pattern, different policy file.

---

## Questions you will get, and the answers

**"Why not just use LiteLLM as-is?"**
LiteLLM routes. It doesn't decide what the data is. The enforcement value is the
classification happening in-process, before egress, and being independent of the
caller's assertion. That's the hook, not the proxy.

**"Why not an LLM to classify PHI?"**
Because sending text to a model to find out whether it's PHI means the PHI has
already left. Detection has to be local. This is the design point I'd defend
hardest.

**"What's the latency cost?"**
Presidio adds a few tens of milliseconds on typical prompt sizes — the audit
record carries `detect_ms`, so pull a real number from your own run rather than
quoting mine.

**"Would this pass an audit?"**
No, and it isn't meant to. It demonstrates the control surface. Certification
needs the de-ID implementation, HA, key management, and a BAA conversation that
belongs to Oracle Legal, not to me.

**"How long did this take?"**
Answer honestly. Part of the argument is that a field engineer can stand this up
in days, which is what makes it repeatable for the guild rather than a one-off.

---

## What to ask for at the end

Don't just present. Name the next artifact:

> "If this is useful, the version worth building is a reference pattern the
> field can clone — policy file, hook, network template — so any SE can run
> this conversation with a regulated customer without rebuilding it. That's
> what I'd want to work on with the guild."
