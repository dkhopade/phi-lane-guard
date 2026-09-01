# PHI Lane Guard — a residency-enforcement PoC on OCI

**What this proves:** that where inference runs for regulated data can be enforced
by policy and network topology, not by developer discipline — and that every
decision leaves an audit record.

**What this is not:** production-ready. No HA, no real de-identification, no BAA
claim. See *Honest limitations* at the end and say those out loud.

---

## The demo, in thirty seconds

Three artifacts, in this order:

1. A request carrying synthetic PHI, sent to an external model, returns
   `403 POLICY_DENY / PHI_LANE_VIOLATION`.
2. The same request with the caller claiming `x-data-class: non-phi` returns the
   *same* denial, with `claim_overridden: true`. Classification is independent of
   what the caller asserts.
3. `curl` from the model host to the public internet times out, because its
   subnet has no route to one.

Everything below exists to make those three moments real.

---

## Architecture

```
   client ──► LiteLLM gateway (public subnet)
                 │
                 │  pre-call hook: Presidio NER, in-process, before any egress
                 │
                 ├── PHI ──────► vLLM on A10  (private subnet, NO internet route)
                 │
                 ├── no PHI ───► external frontier model (over IGW)
                 │
                 └── PHI + external requested ──► 403, fail closed

              every branch ──► audit.jsonl ──► Object Storage (versioned)
```

The gateway sits in the public subnet and can reach both lanes. The model host
sits in a private subnet whose route table is empty. That asymmetry is the
control.

---

> **Verified end to end** on OCI us-ashburn-1: five scenarios, two lanes, sealed
> private subnet, `PHI executed outside the tenancy: 0`. Write-up:
> <https://dkhopade.github.io/phi-lane-guard/>

## Two phases

| Phase | What it is | Where |
|---|---|---|
| **1 · Proof of concept** | Purpose-built gateway on OCI. Two lanes, sealed private subnet, the control proved end to end. | this directory |
| **2 · Native guardrail** | The same control as a registered LiteLLM `CustomGuardrail` — the way a real platform would consume it. | [`litellm-guardrail/`](litellm-guardrail/) |
| **Deployment** | Running it inside an existing LiteLLM on Kubernetes. | [`oke/`](oke/) |
| **Findings** | What testing surfaced that documentation did not. | [`findings/`](findings/) |

Phase 1 answers *can this control be built?* Phase 2 answers *does it survive
contact with the platform it has to live in?*

## Prerequisites

- OCI CLI configured (`oci setup config`), A10 quota confirmed
- An SSH keypair at `~/.ssh/id_rsa.pub`
- A HuggingFace token with Llama access (repos are gated)
- An API key for one external provider (frontier lane)

```bash
export COMPARTMENT_OCID=ocid1.compartment.oc1..xxxxx
export SSH_PUBKEY=$HOME/.ssh/id_rsa.pub
```

> **Billing.** The A10 instance is the only expensive resource here and it bills
> per hour from launch until termination, running or not — the boot volume bills
> too. Check current pricing for your region. Budget for the build (weights pull
> is 10–20 min) plus rehearsal plus the demo itself. **Run `infra/teardown.sh`
> the moment you are done.** Set a calendar reminder now, before you start.

---

## Step 1 — Create the network

```bash
cd infra && ./01-network.sh
```

**Expect:** OCIDs for a VCN, two subnets, three route tables, written to
`infra/.state`. Takes about two minutes.

**Why this matters:** you create *two* route tables for the private subnet —
`rt-private-build` with a NAT route, and `rt-private-sealed` with no rules at
all. The subnet starts attached to `build` because the GPU host needs egress to
download model weights. Sealing is a later, separate, visible act. This is the
single most important design decision in the PoC: the security control is a
route table you can show on screen, not a line of code someone has to trust.

---

## Step 2 — Launch the instances

```bash
./02-instances.sh
```

**Expect:** a gateway VM with a public IP and a GPU VM with a private IP only.
Five to ten minutes. **The GPU meter starts here.**

**Why this matters:** the GPU instance is launched with `--assign-public-ip
false` into a subnet created with `--prohibit-public-ip-on-vnic true`. Even if
someone later attaches an internet route, no public address exists on that VNIC.
Two independent controls, which is what a security reviewer wants to see.

---

## Step 3 — Bring up vLLM (while egress still exists)

```bash
source infra/.state
scp model/vllm-up.sh opc@$GW_IP:~/            # stage via the gateway
ssh opc@$GW_IP "scp vllm-up.sh opc@$GPU_IP:~/"
ssh -J opc@$GW_IP opc@$GPU_IP
export HF_TOKEN=hf_xxxxx
./vllm-up.sh
```

**Expect:** `nvidia-smi` shows one A10. Weights download runs 10–20 minutes.
Watch `sudo podman logs -f vllm` until `Application startup complete`.

**Why this matters:** order is deliberate. Pull the weights *before* sealing. A
common failure in demos like this is sealing first, then discovering the model
host can't fetch anything — you then either unseal in front of the audience or
lose the demo. Do the irreversible-looking step last.

**Verify before moving on:**
```bash
curl -s localhost:8000/v1/models | head
```

---

## Step 4 — Deploy the gateway

```bash
exit   # back to your laptop
scp -r gateway opc@$GW_IP:~/phi-gateway
ssh opc@$GW_IP
sudo dnf install -y docker podman-docker
cd phi-gateway
cp .env.example .env
```

Edit `.env`: set `ANTHROPIC_API_KEY`, set `VLLM_API_BASE=http://<GPU_IP>:8000/v1`,
set a `LITELLM_MASTER_KEY`.

```bash
./run-podman.sh
```

> **Gotcha:** on Oracle Linux 8, `docker compose` is absent and `podman-compose`
> installs but will not run — OL8 ships python3.6, which raises a `SyntaxError`
> on the walrus operator in current podman-compose. `run-podman.sh` skips compose
> entirely. Also create `./audit` first; older podman will not auto-create a
> bind-mount source directory.

**Expect:** the build takes several minutes (spaCy's `en_core_web_lg` is ~500 MB).
Look for LiteLLM binding on `0.0.0.0:4000`.

**Why this matters:** the Presidio model ships *inside* the gateway image. The
classifier has no network dependency — it cannot fail open because a
classification service was unreachable. If detection dies, the container dies,
and nothing routes.

**Verify:**
```bash
curl -s localhost:4000/health/readiness
```

---

## Step 5 — Rehearse both lanes, unsealed

```bash
# from your laptop
cd demo && pip install -r requirements.txt
export GATEWAY=http://$GW_IP:4000
export LITELLM_MASTER_KEY=<the key you set>
python scenarios.py 1 2
```

**Expect:** scenario 1 answered by the external model, scenario 2 by
`intenancy-llama`. Both green.

**Why this matters:** prove the plumbing before you prove the policy. If a
denial appears here, you can't tell whether policy worked or a lane was simply
broken.

---

## Step 6 — Run the denials

```bash
python scenarios.py 3 4 5
```

**Expect:**

- **3** → `403 PHI_LANE_VIOLATION`, detected entities listed
- **4** → the same 403, plus `"claim_overridden": true`
- **5** → allowed, auto-routed to the frontier lane

**Why this matters:** scenario 4 is the one that lands. A developer sending
`x-data-class: non-phi` on text that contains a name and an MRN is not a
hypothetical — it is the normal failure mode, because people mislabel things.
The gateway classifies independently and ignores the assertion. That is the
difference between governance and paperwork.

---

## Step 7 — Seal the subnet

```bash
cd ../infra && ./03-seal-subnet.sh seal
ssh -J opc@$GW_IP opc@$GPU_IP 'curl -m 8 -sS https://api.anthropic.com || echo BLOCKED'
```

**Expect:** the curl hangs for eight seconds, then `BLOCKED`. Then re-run
`python scenarios.py 2` — the in-tenancy lane still answers, because the gateway
reaches it over the VCN.

**Why this matters:** this is the strongest artifact in the whole PoC and it is
not code. Show the route table in the console with zero rules next to a working
in-tenancy inference call. "PHI cannot leave" stops being an assurance and
becomes an observable property.

---

## Step 8 — Produce the compliance answer

```bash
./04-audit-bucket.sh
./05-push-audit.sh
cd ../demo && python audit_report.py ../gateway/audit/audit.jsonl
```

**Expect:** a decision table, then the summary line
`PHI executed outside the tenancy: 0`.

**Why this matters:** that line is the question a privacy office actually asks,
answered as a query rather than a log investigation. Pair it with the Object
Storage bucket showing versioning and a retention rule.

---

## Step 9 — Tear down

```bash
cd ../infra && ./teardown.sh
```

Then check the console manually for surviving **boot volumes** and the **audit
bucket** — neither is removed automatically.

---

## Honest limitations — say these before you are asked

Being first to your own gaps is most of what separates a credible field PoC from
an oversold prototype.

1. **This is not HIPAA Safe Harbor.** Safe Harbor enumerates 18 identifier
   classes. This detects a subset. Full-face imagery, biometrics and device
   identifiers are out of scope entirely.
2. **The de-identification lane is a stub.** `deid_applied` is always `false`.
   The architecture has the seam; the implementation does not fill it.
3. **False positives are real, and I chose that direction deliberately.** In
   testing, "call the on-call cardiologist at 919-555-0143" classifies as PHI —
   a staff phone number is not patient information. So does a bare clinician
   name. The system over-denies rather than under-denies. That is the correct
   posture for a control, but it means a production version needs context rules,
   an allow-list, and a documented exception path.
4. **One detection bug found only by testing the classifier in isolation.**
   Presidio's phone recognizer returns a flat 0.4 confidence for US numbers,
   below the 0.5 global floor — phone numbers were being silently missed. Fixed
   with a per-entity threshold in `policy.yaml`. The lesson generalizes: a
   guardrail that fails silently is worse than none, so test the classifier
   separately from the pipeline.
5. **Overlapping recognizers can attribute a hit to the wrong entity.** A test
   SSN was matched as `PHONE_NUMBER`. The routing decision was still correct,
   but the audit record named the wrong identifier type — which matters if the
   audit trail is the compliance artifact.
6. **No HA.** Single gateway instance. It is a demo.
7. **Managed OCI GenAI models are configured but not on the demo path**, by
   choice — see the note below.

---

## A deliberate omission

The frontier lane points at an external provider rather than an OCI-managed
model. That is not an oversight and it is worth explaining if asked: keeping a
live demo off a dependency with known callability issues is a judgment call, not
a gap in the architecture. The lane is provider-agnostic by construction —
adding a managed model is a `model_list` entry and a line in `policy.yaml`.

---

## Files

```
infra/01-network.sh       VCN, subnets, the two route tables
infra/02-instances.sh     gateway VM + A10 GPU VM
infra/03-seal-subnet.sh   seal | unseal — the demo switch
infra/04-audit-bucket.sh  versioned bucket + retention rule (unlocked, on purpose)
infra/05-push-audit.sh    ship audit records to Object Storage
infra/teardown.sh         stop the meter

gateway/policy.yaml       the lane rules — reviewable without reading code
gateway/config.yaml       LiteLLM model list
gateway/hooks/phi_guard.py  classification + enforcement + audit
gateway/Dockerfile        classifier baked in, no network dependency

model/vllm-up.sh          vLLM on the GPU host

demo/scenarios.py         the five requests
demo/audit_report.py      the compliance answer
```

---

## Test before you spend GPU hours

Both tests run on your laptop with no OCI resources and no gateway:

```bash
cd demo
pip install presidio-analyzer pyyaml phonenumbers
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.7.1/en_core_web_lg-3.7.1-py3-none-any.whl

python test_classify.py       # detection accuracy on clinical text
python test_enforcement.py    # all 7 routing decisions, LiteLLM stubbed out
```

`test_enforcement.py` stubs LiteLLM and FastAPI so the real hook is imported
unchanged, then asserts every allow/deny path and checks that no PHI was
allowed outside the tenancy. Run this after any edit to `policy.yaml` — a
policy change that silently opens a lane is exactly the failure this catches.

> **Version caveat:** `async_pre_call_hook` and `async_post_call_success_hook`
> signatures have moved between LiteLLM releases. `requirements.txt` pins a
> version for that reason. If you unpin and the hook stops firing, check the
> signature against `litellm/integrations/custom_logger.py` in your installed
> version before assuming the policy is broken.
