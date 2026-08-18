"""
PHI lane guard — a LiteLLM proxy hook.

Enforcement point for the residency policy. Runs BEFORE any request leaves
the proxy, which is the entire security argument: classification happens
in-process, so no text is sent anywhere until we know what it is.

Deliberate design choice: PHI detection is a local NER pass (Presidio), NOT
an LLM call. Using a model to decide whether text contains PHI means sending
the PHI to a model before you know it's PHI. That inverts the control.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from litellm.integrations.custom_logger import CustomLogger

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLICY_PATH = Path(os.getenv("POLICY_PATH", "/app/policy.yaml"))
AUDIT_PATH = Path(os.getenv("AUDIT_PATH", "/app/audit/audit.jsonl"))
EXEC_REGION = os.getenv("OCI_REGION", "unknown")

with POLICY_PATH.open() as fh:
    POLICY: dict[str, Any] = yaml.safe_load(fh)

DETECTION = POLICY["detection"]
THRESHOLD = float(DETECTION["score_threshold"])
ENTITY_THRESHOLDS = {
    k: float(v) for k, v in (DETECTION.get("entity_thresholds") or {}).items()
}
ENTITIES = list(DETECTION["entities"])
STRONG = set(DETECTION["strong"])
ON_VIOLATION = POLICY.get("on_violation", "deny")
AUTO = POLICY.get("auto_routing", {})

# model name -> lane name
MODEL_LANE: dict[str, str] = {}
for lane_name, lane in POLICY["lanes"].items():
    for m in lane.get("models", []):
        MODEL_LANE[m] = lane_name

AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

def _build_analyzer() -> AnalyzerEngine:
    engine = AnalyzerEngine()
    # Medical record numbers are site-specific and Presidio has no default
    # recognizer. This pattern covers the common "MRN 12345678" / "MRN-1234567"
    # shapes used by Synthea and most EHR exports.
    mrn = PatternRecognizer(
        supported_entity="MRN",
        patterns=[
            Pattern(name="mrn_labelled", regex=r"\bMRN[-#:\s]{0,3}\d{6,10}\b", score=0.85),
            Pattern(name="mrn_bare", regex=r"\b(?:MR|MRN)\d{6,10}\b", score=0.8),
        ],
        context=["medical", "record", "patient", "chart"],
    )
    engine.registry.add_recognizer(mrn)
    return engine


ANALYZER = _build_analyzer()


def extract_text(data: dict[str, Any]) -> str:
    """Flatten every place a caller could hide text in an OpenAI-shaped body."""
    parts: list[str] = []
    for msg in data.get("messages", []) or []:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            # multimodal content blocks
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    if isinstance(data.get("prompt"), str):
        parts.append(data["prompt"])
    if isinstance(data.get("input"), str):
        parts.append(data["input"])
    return "\n".join(p for p in parts if p)


def classify(text: str) -> tuple[bool, list[dict[str, Any]]]:
    """Return (phi_present, findings)."""
    if not text.strip():
        return False, []
    results = ANALYZER.analyze(text=text, entities=ENTITIES, language="en")
    findings = [
        {"entity": r.entity_type, "score": round(r.score, 3)}
        for r in results
        if r.score >= ENTITY_THRESHOLDS.get(r.entity_type, THRESHOLD)
    ]
    # A bare date or city is not PHI. Require at least one strong identifier.
    phi = any(f["entity"] in STRONG for f in findings)
    return phi, findings


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def write_audit(record: dict[str, Any]) -> None:
    record["ts"] = datetime.now(timezone.utc).isoformat()
    with AUDIT_PATH.open("a") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------

class PHILaneGuard(CustomLogger):

    async def async_pre_call_hook(
        self,
        user_api_key_dict,
        cache,
        data: dict[str, Any],
        call_type: str,
    ):
        req_id = str(uuid.uuid4())
        data.setdefault("metadata", {})["phi_guard_request_id"] = req_id

        headers = (data.get("proxy_server_request") or {}).get("headers", {}) or {}
        claimed = str(headers.get("x-data-class", "unspecified")).lower()
        caller = getattr(user_api_key_dict, "key_alias", None) or getattr(
            user_api_key_dict, "user_id", None
        ) or "unknown"

        requested_model = data.get("model", "")
        text = extract_text(data)

        t0 = time.perf_counter()
        phi, findings = classify(text)
        detect_ms = round((time.perf_counter() - t0) * 1000, 1)

        # --- resolve target lane -------------------------------------------
        if requested_model == "auto":
            lane = AUTO["phi"] if phi else AUTO["non_phi"]
            target_model = POLICY["lanes"][lane]["models"][0]
            routing = "auto"
        else:
            lane = MODEL_LANE.get(requested_model)
            target_model = requested_model
            routing = "explicit"

        audit = {
            "request_id": req_id,
            "caller": caller,
            "claimed_data_class": claimed,
            "detected_phi": phi,
            "detected_entities": sorted({f["entity"] for f in findings}),
            "claim_overridden": (claimed == "non-phi" and phi),
            "requested_model": requested_model,
            "routing": routing,
            "resolved_lane": lane,
            "resolved_model": target_model,
            "execution_region": EXEC_REGION,
            "deid_applied": False,
            "detect_ms": detect_ms,
            "call_type": call_type,
        }

        # --- unknown model: fail closed ------------------------------------
        if lane is None:
            audit["decision"] = "deny"
            audit["reason_code"] = "UNKNOWN_MODEL"
            write_audit(audit)
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "POLICY_DENY",
                    "reason_code": "UNKNOWN_MODEL",
                    "message": f"Model '{requested_model}' is not mapped to any lane.",
                    "request_id": req_id,
                },
            )

        lane_cfg = POLICY["lanes"][lane]

        # --- the enforcement decision --------------------------------------
        if phi and not lane_cfg.get("phi_permitted", False):
            if ON_VIOLATION == "reroute":
                compliant = AUTO["phi"]
                audit.update(
                    decision="reroute",
                    reason_code="PHI_REROUTED",
                    resolved_lane=compliant,
                    resolved_model=POLICY["lanes"][compliant]["models"][0],
                )
                write_audit(audit)
                data["model"] = audit["resolved_model"]
                return data

            audit["decision"] = "deny"
            audit["reason_code"] = "PHI_LANE_VIOLATION"
            write_audit(audit)
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "POLICY_DENY",
                    "reason_code": "PHI_LANE_VIOLATION",
                    "message": (
                        f"PHI detected. Lane '{lane}' does not permit PHI "
                        f"(egress: {lane_cfg.get('egress')}). "
                        f"Detected: {', '.join(audit['detected_entities'])}."
                    ),
                    "claimed_data_class": claimed,
                    "claim_overridden": audit["claim_overridden"],
                    "compliant_lane": AUTO["phi"],
                    "request_id": req_id,
                },
            )

        audit["decision"] = "allow"
        audit["reason_code"] = "OK"
        write_audit(audit)

        data["model"] = target_model
        return data

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        """Second audit line closing the request with token accounting."""
        try:
            req_id = (data.get("metadata") or {}).get("phi_guard_request_id")
            usage = getattr(response, "usage", None)
            write_audit({
                "request_id": req_id,
                "decision": "completed",
                "model_served": getattr(response, "model", None),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
            })
        except Exception as exc:  # never break a successful call on audit failure
            print(f"[phi_guard] post-call audit failed: {exc}")
        return response


phi_guard = PHILaneGuard()
