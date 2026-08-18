"""Exercise the hook's decision logic with LiteLLM/FastAPI stubbed out."""
import sys, types, asyncio, json, os, tempfile

# --- stub litellm + fastapi so we can import the real hook unchanged --------
class HTTPException(Exception):
    def __init__(self, status_code, detail): self.status_code, self.detail = status_code, detail
fa = types.ModuleType("fastapi"); fa.HTTPException = HTTPException
sys.modules["fastapi"] = fa
ll = types.ModuleType("litellm"); ic = types.ModuleType("litellm.integrations")
cl = types.ModuleType("litellm.integrations.custom_logger")
class CustomLogger: pass
cl.CustomLogger = CustomLogger
sys.modules.update({"litellm": ll, "litellm.integrations": ic,
                    "litellm.integrations.custom_logger": cl})

audit_file = tempfile.mktemp(suffix=".jsonl")
os.environ["POLICY_PATH"] = "../gateway/policy.yaml"
os.environ["AUDIT_PATH"] = audit_file
os.environ["OCI_REGION"] = "us-ashburn-1"

sys.path.insert(0, "../gateway")
from hooks.phi_guard import phi_guard

PHI = ("Patient Marcus Delgado, MRN 4471822, DOB 1968-03-14, reachable at "
       "919-555-0177. Echo shows EF 38%.")
CLEAN = "Summarize first-line management of HFrEF. Drug classes only."

class Key: key_alias = "team-cardiology"

def req(model, cls, content):
    return {"model": model,
            "messages": [{"role":"user","content":content}],
            "proxy_server_request": {"headers": {"x-data-class": cls}}}

async def call(model, cls, content):
    try:
        d = await phi_guard.async_pre_call_hook(Key(), None, req(model,cls,content), "completion")
        return ("ALLOW", d["model"])
    except HTTPException as e:
        return ("DENY", e.detail["reason_code"])

CASES = [
 (1,"frontier-claude","non-phi",CLEAN, "ALLOW","frontier-claude"),
 (2,"intenancy-llama","phi",PHI,       "ALLOW","intenancy-llama"),
 (3,"frontier-claude","phi",PHI,       "DENY","PHI_LANE_VIOLATION"),
 (4,"frontier-claude","non-phi",PHI,   "DENY","PHI_LANE_VIOLATION"),
 (5,"auto","unspecified",CLEAN,        "ALLOW","frontier-claude"),
 (6,"auto","unspecified",PHI,          "ALLOW","intenancy-llama"),
 (7,"nonexistent-model","phi",CLEAN,   "DENY","UNKNOWN_MODEL"),
]
ok = True
for n, model, cls, content, exp_d, exp_v in CASES:
    d, v = asyncio.run(call(model, cls, content))
    good = (d == exp_d and v == exp_v)
    ok &= good
    print(f"{'PASS' if good else 'FAIL'}  s{n}  {model:18s} claim={cls:11s} -> {d:5s} {v}")

print()
recs = [json.loads(l) for l in open(audit_file)]
print(f"audit records written: {len(recs)}")
ov = [r for r in recs if r.get("claim_overridden")]
print(f"claim_overridden flagged on: {len(ov)} record(s)  <- scenario 4")
leaks = [r for r in recs if r.get("detected_phi") and r.get("decision")=="allow"
         and r.get("resolved_lane")!="in_tenancy"]
print(f"PHI allowed outside tenancy: {len(leaks)}")
print("\nSample audit record:")
print(json.dumps(recs[3], indent=2))
print("\n" + ("ALL ENFORCEMENT TESTS PASSED" if ok and not leaks else "FAILURES PRESENT"))
