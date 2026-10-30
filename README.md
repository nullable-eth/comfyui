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

## MCP server (`mcp/comfyui_mcp.py`)

A local stdio MCP server for any MCP client (Claude desktop, etc.). It runs on
the machine that holds the images: tools take local file paths, upload them,
run the job and save the results back to local files, returning a preview.

Tools: `render_archviz` (depth map + optional line map + optional style
image + prompt -> photoreal image; inputs are scaled to ~1 MP, sides multiples
of 64), `run_workflow` (any API-format workflow), `get_result` (poll a job
that outlived the wait), `comfyui_status` (GPU, queue, models).

Self-contained via `uv` (dependencies pinned inline):

```json
"comfyui": {
  "command": "uv",
  "args": ["run", "--script", "/path/to/comfyui/mcp/comfyui_mcp.py"],
  "env": {
    "COMFYUI_URL": "https://comfyui.example.com",
    "COMFYUI_CLIENT_CERT": "/path/to/client.pem",
    "COMFYUI_OUTPUT_DIR": "/path/to/renders"
  }
}
```

`COMFYUI_CLIENT_CERT` (a PEM holding cert + key) is only for a reverse proxy
that requires mTLS; leave it out for a plain `http://host:8188`.
