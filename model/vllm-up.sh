#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Runs ON THE GPU HOST. Brings up vLLM serving an OpenAI-compatible API.
#
# Run this BEFORE sealing the subnet — the weights download needs egress.
# Once weights are on the boot volume, sealing costs you nothing.
# ---------------------------------------------------------------------------
set -euo pipefail

MODEL="${MODEL:-meta-llama/Llama-3.1-8B-Instruct}"
HF_TOKEN="${HF_TOKEN:-none}"   # gated repos need a real token + accepted licence

echo "==> nvidia-smi sanity check"
nvidia-smi || { echo "No GPU visible. Wrong image? Use a GPU-enabled OL build."; exit 1; }

echo "==> Growing the filesystem to the full boot volume"
# GOTCHA: --boot-volume-size-in-gbs allocates the block volume but does NOT
# expand the partition or filesystem. Without this, the vLLM image alone
# exhausts the default ~39 GB root and the pull dies mid-layer.
sudo /usr/libexec/oci-growfs -y || true
df -h /

echo "==> Installing container runtime"
sudo dnf install -y podman >/dev/null 2>&1 || sudo yum install -y podman

echo "==> NVIDIA container toolkit + legacy OCI prestart hook"
# GOTCHA: the A10 shape image may be Oracle Linux 7 with podman 1.6.4, which
# predates CDI entirely -- "--device nvidia.com/gpu=all" fails with
# "cannot stat nvidia.com/gpu=all". The legacy prestart hook works on both.
sudo yum install -y nvidia-container-toolkit >/dev/null 2>&1 || true
sudo mkdir -p /usr/share/containers/oci/hooks.d
sudo tee /usr/share/containers/oci/hooks.d/oci-nvidia-hook.json >/dev/null <<'HOOK'
{
  "version": "1.0.0",
  "hook": {
    "path": "/usr/bin/nvidia-container-runtime-hook",
    "args": ["nvidia-container-runtime-hook", "prestart"],
    "env": []
  },
  "when": { "always": true, "commands": [".*"] },
  "stages": ["prestart"]
}
HOOK

mkdir -p /home/opc/hfcache

echo "==> Opening port 8000 to the VCN only"
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=10.0.0.0/16 port port=8000 protocol=tcp accept'
sudo firewall-cmd --reload

echo "==> Pulling weights and starting vLLM (first run takes 10-20 min)"
sudo podman run -d --name vllm \
  --security-opt=label=disable \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -p 8000:8000 \
  -e HF_TOKEN="$HF_TOKEN" \
  -v /home/opc/hfcache:/root/.cache/huggingface:Z \
  docker.io/vllm/vllm-openai:latest \
  --model "$MODEL" \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90

echo
echo "Follow the load:  sudo podman logs -f vllm"
echo "Ready when you see: 'Application startup complete'"
echo "Verify:  curl -s localhost:8000/v1/models | head"
