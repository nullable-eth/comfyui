# ComfyUI for the Whitehorse V100s (sm_70).
#
# Base: the same PyTorch image wyoming-kokoro already runs on these cards
# (torch 2.10.0+cu128, arch list includes sm_70), so it is cached on
# whitehorse-ai and known to work on a V100. ComfyUI's own requirements are
# installed WITHOUT torch/torchvision/torchaudio so pip cannot swap that build
# for one that drops Volta.
#
# Everything is pinned: ComfyUI to a release tag, custom nodes to a commit, and
# the resolved Python packages are frozen into /opt/pip-freeze.txt at build time
# so a rebuild can be made identical (a floating rebuild of another image broke
# it in Oct 2026).
FROM pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime

ARG COMFYUI_REF=v0.43.2
ARG IPADAPTER_PLUS_REF=a0f451a5113cf9becb0847b92884cb10cbdec0ef

RUN apt-get update \
 && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
 && apt-get install -y --no-install-recommends --only-upgrade ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/comfyanonymous/ComfyUI /opt/ComfyUI \
 && git -C /opt/ComfyUI checkout "$COMFYUI_REF" \
 && git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus /opt/ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus \
 && git -C /opt/ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus checkout "$IPADAPTER_PLUS_REF"

RUN grep -viE '^(torch|torchvision|torchaudio)([<>=~! \[]|$)' /opt/ComfyUI/requirements.txt > /tmp/req.txt \
 && pip install --no-cache-dir --break-system-packages -r /tmp/req.txt "certifi>=2026.10.19" \
 && python -c "import torch; assert 'sm_70' in torch.cuda.get_arch_list(), torch.cuda.get_arch_list(); print('torch', torch.__version__)" \
 && pip freeze > /opt/pip-freeze.txt

COPY workflows/ /opt/workflows/
WORKDIR /opt/ComfyUI
EXPOSE 8188
ENTRYPOINT ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188", "--disable-auto-launch"]
