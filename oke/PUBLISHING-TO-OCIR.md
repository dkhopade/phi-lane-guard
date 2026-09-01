# Publishing the image to OCIR

How to build the guardrail image, push it to Oracle Cloud Infrastructure
Registry, and give someone else access to pull it.

---

## Before you start

You need:

- The LiteLLM image tag the **recipient** is running, not yours. Ask them:
  ```bash
  kubectl get deploy <name> -o jsonpath='{.spec.template.spec.containers[0].image}'
  ```
- Your tenancy namespace: `oci os ns get --query data --raw-output`
- An OCI **auth token** for registry login. This is not your console password
  and not an API key — generate one at Profile → My profile → Auth tokens.
  **Copy it immediately; it is shown once.**

---

## Step 1 — Set the base image to match theirs

```bash
cd oke
vi Dockerfile   # set FROM to the tag they run
```

**Why this matters:** the image extends theirs rather than replacing it. A
mismatch means you hand them a gateway on a different LiteLLM version than the
one they tested, which is a change they did not ask for and will not expect.

## Step 2 — Log in to OCIR

The registry host is region-specific — `iad.ocir.io` for Ashburn, `phx.ocir.io`
for Phoenix, and so on.

```bash
export OCI_REGION_KEY=iad
export TENANCY_NS=$(oci os ns get --query data --raw-output)
export OCIR_USER="$TENANCY_NS/<your-oracle-email>"

docker login ${OCI_REGION_KEY}.ocir.io -u "$OCIR_USER"
# password: the auth token from above
```

For a federated tenancy the username is
`<namespace>/oracleidentitycloudservice/<your-email>`. If login fails with
otherwise-correct credentials, that prefix is the usual reason.

## Step 3 — Build and push

Tag with a real version. **Never `latest`** for something another team will
deploy — they need to know what they are running, and `latest` moves under
them.

```bash
export IMAGE=${OCI_REGION_KEY}.ocir.io/${TENANCY_NS}/phi-lane-guard

docker build -t ${IMAGE}:1.0.0 .
docker push ${IMAGE}:1.0.0
```

The build needs egress to PyPI and to spaCy's model host on GitHub. Roughly
600 MB of layers on top of the base image.

## Step 4 — Record what you shipped

```bash
docker inspect ${IMAGE}:1.0.0 --format '{{.Id}}'
docker run --rm --entrypoint sh ${IMAGE}:1.0.0 -c \
  'python -c "import spacy, presidio_analyzer; print(spacy.__version__, presidio_analyzer.__version__)"'
```

Send both to the recipient. A digest pins exactly what they are running in a
way a tag does not.

## Step 5 — Give them access

By default an OCIR repository is private to the tenancy.

**Same tenancy** — they need an IAM policy allowing the pull:

```
Allow group <their-group> to read repos in tenancy where target.repo.name = 'phi-lane-guard'
```

Then a Kubernetes pull secret in their namespace:

```bash
kubectl -n <ns> create secret docker-registry ocir-secret \
  --docker-server=iad.ocir.io \
  --docker-username='<namespace>/<their-email>' \
  --docker-password='<their auth token>' \
  --docker-email='<their-email>'
```

Referenced from the pod spec:

```yaml
spec:
  imagePullSecrets:
    - name: ocir-secret
```

**Different tenancy** — cross-tenancy pulls need `Define`/`Endorse`/`Admit`
policies on both sides. Usually slower than it is worth for a proof of
concept. Two alternatives:

- Make the repository public in the OCIR console (Repositories → Actions →
  Change to Public). Fine here: the image contains open-source packages and
  your guardrail code, which is already public on GitHub. It contains **no**
  credentials or policy — `policy.yaml` is mounted from a ConfigMap, not baked.
- Have them build it themselves from the repo. Four files, one command, and
  they end up with an image in their own registry with provenance they control.

**For a guild handoff I would suggest they build it.** It removes the access
problem entirely, and a platform team generally prefers an image they built to
one handed to them.

---

## What to send with it

```
Image:     iad.ocir.io/<ns>/phi-lane-guard:1.0.0
Digest:    sha256:...
Built on:  docker.litellm.ai/berriai/litellm-database:<their tag>
Adds:      presidio-analyzer 2.2.355, spaCy <version>, en_core_web_lg
Docs:      github.com/dkhopade/phi-lane-guard/tree/main/oke
```

Point them at `oke/README.md` for the deployment steps, and flag two things
directly:

**`policy.yaml` is theirs to edit.** `phi_permitted_models` must list their
actual in-tenancy model names. It is an allowlist and deny-by-default — an
empty list denies PHI to everything, which is the safe direction but will look
like a bug if they are not expecting it.

**The guardrail is one of two controls.** It enforces *which model* may receive
PHI. Whether that model can reach the internet is a separate control — the
sealed subnet. Both are needed for the residency claim to hold, and a guardrail
alone would give a false sense of completeness.
