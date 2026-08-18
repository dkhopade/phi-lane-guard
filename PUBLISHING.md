# Publishing this repo

Run these from the project root on your Mac.

## Step 1 — Confirm no secrets are staged

```bash
cd ~/my-labs/david-project/phi-lane-poc
cat .gitignore
grep -rn "sk-ant\|sk-poc\|ocid1\." --include="*.yaml" --include="*.md" --include="*.py" . | grep -v ".venv" | head
```

Expect: `.env`, `.state`, `.bucket`, `audit/` all listed in .gitignore, and no live keys
or OCIDs in tracked files. `.state` holds your tenancy OCIDs and must never be committed.

## Step 2 — Initialise and commit

```bash
rm -rf .venv demo/audit*.jsonl demo/__pycache__
git init
git add .
git status          # read this list before committing
git commit -m "PHI Lane Guard: residency enforcement PoC on OCI"
```

## Step 3 — Create the public repo

On github.com: New repository → name `phi-lane-guard` → Public → do NOT initialise
with a README.

```bash
git remote add origin https://github.com/dkhopade/phi-lane-guard.git
git branch -M main
git push -u origin main
```

## Step 4 — Turn on GitHub Pages

Repo → Settings → Pages → Source: "Deploy from a branch" →
Branch `main`, folder `/docs` → Save.

Live in a couple of minutes at:
https://dkhopade.github.io/phi-lane-guard/

## Step 5 — Set the repo description

Settings → About (gear icon, top right of the repo page):

- Description: `Residency enforced by topology, not convention — a PHI lane-routing gateway on OCI`
- Website: `https://dkhopade.github.io/phi-lane-guard/`
- Topics: `oci` `litellm` `presidio` `vllm` `hipaa` `ai-governance` `data-residency` `llm-gateway`
