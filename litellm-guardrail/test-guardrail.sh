#!/usr/bin/env bash
# Four requests that exercise the guardrail. Run from your dev env directory.
set -uo pipefail
KEY=$(grep LITELLM_MASTER_KEY .env | cut -d= -f2)
MODEL="${MODEL:-claude-sonnet-4-5}"

PHI="Patient Marcus Delgado, MRN 4471822, DOB 1968-03-14, call 919-555-0177."
CLEAN="Summarize first-line pharmacologic management of HFrEF. Drug classes only."

hit () {
  local name="$1" cls="$2" content="$3" expect="$4"
  printf '\n\033[1;36m== %s\033[0m\n   expect: %s\n' "$name" "$expect"
  curl -s -o /tmp/resp.json -w "   HTTP %{http_code}\n" \
    localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -H "x-data-class: $cls" \
    -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$content\"}],\"max_tokens\":60}"
  python3 - <<'PY'
import json
try:
    d = json.load(open('/tmp/resp.json'))
except Exception:
    print("   <unparseable response>"); raise SystemExit
err = d.get("error")
if isinstance(err, dict) and "message" in err:
    m = err["message"]
    try: m = json.loads(m) if isinstance(m, str) and m.startswith("{") else m
    except Exception: pass
    print("  ", json.dumps(m, indent=2)[:700].replace("\n", "\n   "))
elif "choices" in d:
    print("   ALLOWED:", d["choices"][0]["message"]["content"][:90].replace("\n"," "), "...")
else:
    print("  ", json.dumps(d)[:400])
PY
}

hit "1. Clean text to external model"      "non-phi"  "$CLEAN" "ALLOW"
hit "2. PHI to external model"             "phi"      "$PHI"   "DENY - PHI_LANE_VIOLATION"
hit "3. PHI mislabelled as non-phi"        "non-phi"  "$PHI"   "DENY - claim_overridden: true"
hit "4. PHI with no header at all"         "" \
                                           "$PHI"   "DENY - absence of a claim is not permission"

printf '\n\033[1;36m== guardrail decisions recorded by LiteLLM\033[0m\n'
docker compose exec -T db psql -U litellm -d litellm -c \
 "SELECT LEFT(request_id,18) AS req, status,
         metadata->'applied_guardrails' AS applied
  FROM \"LiteLLM_SpendLogs\" ORDER BY \"startTime\" DESC LIMIT 4;" 2>/dev/null \
 || echo "   (spend log query skipped)"
