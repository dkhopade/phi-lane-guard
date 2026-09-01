#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Verify PHI Lane Guard against any LiteLLM endpoint.
#
#   ./test-guardrail.sh https://gateway.example.com sk-your-key model-name
#
# Works against a local dev proxy or a cluster ingress. No cluster access
# required - it only calls the public API.
# ---------------------------------------------------------------------------
set -uo pipefail

BASE="${1:?usage: $0 <base-url> <api-key> <model-name>}"
KEY="${2:?missing api key}"
MODEL="${3:?missing model name}"

PHI="Patient Marcus Delgado, MRN 4471822, DOB 1968-03-14, call 919-555-0177."
CLEAN="Summarize first-line pharmacologic management of HFrEF. Drug classes only."

pass=0; fail=0

hit () {
  local name="$1" cls="$2" content="$3" expect_code="$4" why="$5"
  code=$(curl -s -o /tmp/phi_resp.json -w "%{http_code}" \
    "$BASE/v1/chat/completions" \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    ${cls:+-H "x-data-class: $cls"} \
    -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$content\"}],\"max_tokens\":40}")

  if [ "$code" = "$expect_code" ]; then
    printf '\033[32mPASS\033[0m  %-42s HTTP %s\n' "$name" "$code"; pass=$((pass+1))
  else
    printf '\033[31mFAIL\033[0m  %-42s HTTP %s (expected %s)\n' "$name" "$code" "$expect_code"; fail=$((fail+1))
  fi
  echo "      $why"
  python3 - <<'PY'
import json
try: d=json.load(open('/tmp/phi_resp.json'))
except Exception: raise SystemExit
p=(d.get("error") or {}).get("provider_specific_fields") or {}
if p.get("reason_code"):
    print("      reason:", p["reason_code"],
          "| entities:", ",".join(p.get("detected_entities") or []),
          "| claim_overridden:", p.get("claim_overridden"))
PY
  echo
}

echo
echo "Endpoint: $BASE   Model: $MODEL"
echo

hit "1. clean text"                   "non-phi" "$CLEAN" 200 \
    "no identifiers - should route normally"
hit "2. PHI, honestly labelled"       "phi"     "$PHI"   403 \
    "identifiers present, model not PHI-permitted"
hit "3. PHI mislabelled as non-phi"   "non-phi" "$PHI"   403 \
    "the caller's claim is evidence, not authority"
hit "4. PHI with no data-class header" ""       "$PHI"   403 \
    "absence of a claim is not permission"

echo "---------------------------------------------"
printf 'passed %d, failed %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
