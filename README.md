# comfyui

ComfyUI image for the Whitehorse cluster's V100s (sm_70), with
[ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus) and the
archviz workflow in `workflows/` (also at `/opt/workflows/` in the image).

- Base `pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime`: torch 2.10.0+cu128 with
  sm_70 (the build asserts it). ComfyUI's requirements are installed without
  torch so pip cannot replace that build.
- ComfyUI pinned to a release tag, the custom node to a commit; the resolved
  packages are frozen to `/opt/pip-freeze.txt`.
- Models and the deployment live in nullable-eth/Whitehorse
  (`kubernetes/apps/ai/comfyui`), whose README documents the workflow and API.
