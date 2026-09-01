# ---------------------------------------------------------------------------
# PHI Lane Guard — extends the stock LiteLLM image with the Presidio classifier.
#
# IMPORTANT: set FROM to the tag you are ALREADY running, not latest. Check with
#   kubectl get deploy <name> -o jsonpath='{..containers[0].image}'
#
# Two constraints this image forces, both discovered the hard way:
#   1. The stock venv ships WITHOUT pip (built with uv and stripped), so pip has
#      to be bootstrapped via ensurepip before anything can be installed.
#   2. It runs Python 3.13, so pip resolves spaCy 3.8.x rather than 3.7.x. The
#      language model is fetched with `spacy download` rather than a pinned URL,
#      because a pinned URL fails on a spaCy major-version mismatch.
#
# The model is baked in deliberately: the guardrail then has no network
# dependency and cannot fail open because a detection service was unreachable.
# If it cannot load, the container does not start.
# ---------------------------------------------------------------------------
FROM docker.litellm.ai/berriai/litellm-database:latest

USER root

COPY requirements.txt /tmp/requirements.txt
RUN python -m ensurepip --upgrade \
 && python -m pip install --no-cache-dir -r /tmp/requirements.txt \
 && python -m spacy download en_core_web_lg \
 && rm -rf /root/.cache

# The guardrail itself is baked; policy.yaml is mounted from a ConfigMap so it
# can be reviewed and changed without rebuilding the image.
COPY phi_guardrail.py /app/guardrails/phi_guardrail.py

ENV PHI_POLICY_PATH=/app/guardrails/policy.yaml
ENV PYTHONPATH=/app/guardrails:$PYTHONPATH

# Restore the non-root user the base image runs as. If your cluster enforces a
# specific runAsUser, set it in the pod securityContext instead.
USER 1000
