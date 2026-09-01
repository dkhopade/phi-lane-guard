"""
PHI Lane Guard — a native LiteLLM custom guardrail.

WHAT CHANGED FROM THE POC
-------------------------
The PoC registered as a CustomLogger callback. This registers as a
CustomGuardrail, which is the idiomatic surface and brings three things a
platform needs:

  1. Per-key and per-team configuration — a guardrail can be enabled for some
     tenants and not others, which a global callback cannot do.
  2. Native decision logging — the @log_guardrail_information decorator writes
     the verdict into LiteLLM's own spend log (the `applied_guardrails` and
     `guardrail_information` fields). No separate audit ledger to correlate.
  3. Per-request invocation — callers can name guardrails in the request body,
     and the platform can force them on with `default_on: true`.

WHY THIS COMPONENT EXISTS
-------------------------
Detection is a local NER pass (Presidio), never an LLM call: asking a model
whether text contains PHI means the PHI has already left the boundary.

The caller's own data-class assertion is recorded as evidence, never obeyed.
The realistic failure is not a malicious caller — it is a developer who does
not know whether a free-text clinical note contains identifiers.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from fastapi import HTTPException

from litellm._logging import verbose_proxy_logger
from litellm.caching.caching import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.proxy._types import UserAPIKeyAuth

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer

# The decorator that writes this guardrail's verdict into LiteLLM's spend log.
# Import path has moved between releases, so degrade gracefully rather than
# failing to load the whole guardrail over a logging nicety.
try:
    from litellm.proxy.guardrails.guardrail_hooks.custom_guardrail import (
        log_guardrail_information,
    )
except Exception:  # noqa: BLE001
    def log_guardrail_information(func):  # type: ignore[misc]
        return func


POLICY_PATH = Path(os.getenv("PHI_POLICY_PATH", "/app/guardrails/policy.yaml"))


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def _load_policy() -> dict[str, Any]:
    with POLICY_PATH.open() as fh:
        return yaml.safe_load(fh)


POLICY = _load_policy()
DET = POLICY["detection"]
THRESHOLD = float(DET["score_threshold"])
ENTITY_THRESHOLDS = {k: float(v) for k, v in (DET.get("entity_thresholds") or {}).items()}
ENTITIES = list(DET["entities"])
STRONG = set(DET["strong"])
PHI_PERMITTED_MODELS = set(POLICY["lanes"]["phi_permitted_models"])


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _build_analyzer() -> AnalyzerEngine:
    engine = AnalyzerEngine()
    # Presidio ships recognizers for universal identifiers only. Site-specific
    # formats (MRN, accession numbers, trial subject IDs) are invisible until
    # someone writes a pattern for them.
    engine.registry.add_recognizer(PatternRecognizer(
        supported_entity="MRN",
        patterns=[
            Pattern("mrn_labelled", r"\bMRN[-#:\s]{0,3}\d{6,10}\b", 0.85),
            Pattern("mrn_bare", r"\b(?:MR|MRN)\d{6,10}\b", 0.8),
        ],
        context=["medical", "record", "patient", "chart"],
    ))
    return engine


ANALYZER = _build_analyzer()


def _extract_text(data: dict) -> str:
    """Flatten every field a caller could hide text in."""
    parts: list[str] = []
    for msg in data.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    for field in ("prompt", "input"):
        if isinstance(data.get(field), str):
            parts.append(data[field])
    return "\n".join(p for p in parts if p)


def _classify(text: str) -> tuple[bool, list[str]]:
    """Return (phi_present, detected_entity_types)."""
    if not text.strip():
        return False, []
    results = ANALYZER.analyze(text=text, entities=ENTITIES, language="en")
    found = [
        r.entity_type for r in results
        if r.score >= ENTITY_THRESHOLDS.get(r.entity_type, THRESHOLD)
    ]
    # A bare date or city is not PHI. Require at least one strong identifier,
    # or the guardrail over-denies into uselessness.
    return any(e in STRONG for e in found), sorted(set(found))




def _suggested_model() -> Optional[str]:
    """The lane a developer should use instead. None if policy permits nothing."""
    return sorted(PHI_PERMITTED_MODELS)[0] if PHI_PERMITTED_MODELS else None


def classify_payload(text: str, requested_model: str = "") -> dict:
    """Shared by the enforcement hook and the /classify dry-run endpoint.

    One code path means the dry-run cannot drift from what enforcement actually
    does - a dry-run that disagrees with the real decision is worse than none.
    """
    t0 = time.perf_counter()
    phi, entities = _classify(text)
    detect_ms = round((time.perf_counter() - t0) * 1000, 1)

    data_class = "phi" if phi else "public"
    allowed = (not phi) or (requested_model in PHI_PERMITTED_MODELS)

    return {
        "data_class": data_class,
        "detected_entities": entities,
        "would_be_allowed": allowed,
        "requested_model": requested_model or None,
        "required_lane": "in-tenancy" if phi else "unrestricted",
        "phi_permitted_models": sorted(PHI_PERMITTED_MODELS),
        "suggested_model": None if allowed else _suggested_model(),
        "detect_ms": detect_ms,
    }


# ---------------------------------------------------------------------------
# Guardrail
# ---------------------------------------------------------------------------

class PHILaneGuard(CustomGuardrail):
    """Blocks PHI-bearing requests aimed at models not permitted to see PHI."""

    def __init__(self, **kwargs):
        self.optional_params = kwargs
        super().__init__(**kwargs)
        verbose_proxy_logger.info(
            "PHILaneGuard loaded. PHI-permitted models: %s", sorted(PHI_PERMITTED_MODELS)
        )

    @log_guardrail_information
    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: Optional[str] = None,
    ) -> Optional[Union[Exception, str, dict]]:

        headers = (data.get("proxy_server_request") or {}).get("headers", {}) or {}
        asserted = str(headers.get("x-data-class", "unspecified")).lower()
        requested_model = data.get("model", "")

        t0 = time.perf_counter()
        phi, entities = _classify(_extract_text(data))
        detect_ms = round((time.perf_counter() - t0) * 1000, 1)

        # The caller said one thing; the classifier determined another.
        claim_overridden = phi and asserted in ("non-phi", "public", "unspecified")

        verbose_proxy_logger.info(
            "PHILaneGuard: model=%s asserted=%s detected_phi=%s entities=%s %.1fms",
            requested_model, asserted, phi, entities, detect_ms,
        )

        if not phi:
            return data

        if requested_model in PHI_PERMITTED_MODELS:
            verbose_proxy_logger.info(
                "PHILaneGuard: PHI allowed - '%s' is a PHI-permitted lane", requested_model
            )
            return data

        # Fail closed. The reason code and the overridden claim are the two
        # fields that matter in a customer conversation.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "POLICY_DENY",
                "reason_code": "PHI_LANE_VIOLATION",
                "message": (
                    f"PHI detected. Model '{requested_model}' is not permitted to "
                    f"process PHI. Detected: {', '.join(entities)}."
                ),
                "detected_entities": entities,
                "claimed_data_class": asserted,
                "claim_overridden": claim_overridden,
                "phi_permitted_models": sorted(PHI_PERMITTED_MODELS),
                # Actionable, not just correct. The guard already knows which
                # lanes are permitted; telling the developer which one to use
                # turns "you are wrong" into "send it here instead".
                "suggested_model": _suggested_model(),
                "how_to_fix": (
                    f"Send this request to '{_suggested_model()}', or remove the "
                    f"identifiers if the model choice is fixed. Dry-run any payload "
                    f"against POST /classify to check before sending."
                ),
                "detect_ms": detect_ms,
            },
        )

    # -----------------------------------------------------------------------
    # Second entry point.
    #
    # LiteLLM invokes guardrails through two different interfaces:
    #
    #   async_pre_call_hook()  - the real request path, /v1/chat/completions
    #   apply_guardrail()      - the newer unified interface, and what the
    #                            "test guardrail" panel in the Admin UI calls
    #
    # The base class implements apply_guardrail() as `return inputs` - a no-op.
    # A guardrail that only implements the hook therefore BLOCKS correctly in
    # production while the UI test panel reports success in ~24 microseconds.
    # The console tells an operator the content is allowed when the API denies
    # it, and the safe-looking answer is the wrong one.
    #
    # Implementing both keeps the two paths honest about each other.
    # -----------------------------------------------------------------------
    @log_guardrail_information
    async def apply_guardrail(
        self,
        inputs: Any,
        request_data: dict,
        input_type: str,
        logging_obj: Any = None,
    ) -> Any:
        """The live enforcement path.

        IMPORTANT: when a guardrail implements both interfaces, LiteLLM calls
        THIS one and async_pre_call_hook never runs. Verified on 1.98.0 - the
        traceback lands here, not in the hook. So this method carries the full
        decision, and the hook below is kept only as a fallback for versions
        that call it instead.

        The inverse is the real trap: a guardrail implementing ONLY the hook
        blocks correctly on the API path while the Admin UI's guardrail test
        panel reports success, because the base apply_guardrail is `return
        inputs` - a no-op. Implement both, keep them consistent.
        """
        texts = []
        try:
            texts = list(inputs.get("texts") or [])
        except AttributeError:
            texts = list(getattr(inputs, "texts", None) or [])
        combined = "\n".join(t for t in texts if isinstance(t, str))

        rd = request_data or {}
        requested_model = rd.get("model", "") or "<none>"
        headers = (rd.get("proxy_server_request") or {}).get("headers", {}) or {}
        asserted = str(headers.get("x-data-class", "unspecified")).lower()

        result = classify_payload(combined, requested_model)

        if result["would_be_allowed"]:
            return inputs

        # The caller said one thing; the classifier determined another. This is
        # the field that matters in a customer conversation.
        claim_overridden = asserted in ("non-phi", "public", "unspecified")
        suggested = result["suggested_model"]

        raise HTTPException(
            status_code=403,
            detail={
                "error": "POLICY_DENY",
                "reason_code": "PHI_LANE_VIOLATION",
                "message": (
                    f"PHI detected. Model '{requested_model}' is not permitted "
                    f"to process PHI. Detected: "
                    f"{', '.join(result['detected_entities'])}."
                ),
                "detected_entities": result["detected_entities"],
                "data_class": result["data_class"],
                "required_lane": result["required_lane"],
                "claimed_data_class": asserted,
                "claim_overridden": claim_overridden,
                "phi_permitted_models": result["phi_permitted_models"],
                "suggested_model": suggested,
                "how_to_fix": (
                    f"Send this request to '{suggested}', or remove the "
                    f"identifiers if the model choice is fixed. Dry-run any "
                    f"payload against POST /guardrails/apply_guardrail to check "
                    f"before sending."
                ),
                "input_type": input_type,
                "detect_ms": result["detect_ms"],
            },
        )
