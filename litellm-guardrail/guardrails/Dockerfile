# Extends the stock LiteLLM image with the Presidio classifier.
#
# The spaCy model is baked into the image deliberately: the guardrail then has
# no network dependency and cannot fail open because a detection service was
# unreachable. If it can't load, the container doesn't start.
FROM docker.litellm.ai/berriai/litellm-database:main-v1.98.0

USER root

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && python -m spacy download en_core_web_lg

COPY phi_guardrail.py policy.yaml /app/guardrails/

ENV PHI_POLICY_PATH=/app/guardrails/policy.yaml
ENV PYTHONPATH=/app/guardrails:$PYTHONPATH
