#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp==1.12.4",
#   "pydantic==2.13.5",
#   "pydantic-core==2.46.5",
#   "httpx==0.28.1",
#   "pillow==12.0.0",
# ]
# ///
"""A small local (stdio) MCP server that drives a ComfyUI instance.

It runs on the machine that holds the images, so tools take LOCAL FILE PATHS
and write results back to local files: nothing big passes through the model.

Environment:
  COMFYUI_URL          ComfyUI base URL (default http://127.0.0.1:8188)
  COMFYUI_CLIENT_CERT  optional PEM (cert + key) for a reverse proxy that
                       requires a client certificate (mTLS)
  COMFYUI_OUTPUT_DIR   where results are saved (default ~/comfyui-renders)
  COMFYUI_WAIT         seconds a tool waits for a job before handing back a
                       prompt_id to poll with get_result (default 240)

Register it with any MCP client, e.g.:
  {"command": "uv", "args": ["run", "--script", "/path/to/comfyui_mcp.py"],
   "env": {"COMFYUI_URL": "https://comfyui.example.com"}}
"""
import hashlib
import io
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage

URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
CERT = os.environ.get("COMFYUI_CLIENT_CERT") or None
OUT = Path(os.path.expanduser(os.environ.get("COMFYUI_OUTPUT_DIR", "~/comfyui-renders")))
WAIT = float(os.environ.get("COMFYUI_WAIT", "240"))
ARCHVIZ = Path(__file__).resolve().parent.parent / "workflows" / "archviz-depth-lines-style.api.json"
CLIENT_ID = str(uuid.uuid4())

http = httpx.Client(base_url=URL, timeout=httpx.Timeout(120, connect=10),
                    cert=os.path.expanduser(CERT) if CERT else None)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


mcp = FastMCP(
    "comfyui",
    instructions=(
        "ComfyUI image generation on a GPU server. Images are passed as local file "
        "paths on this machine and results are saved to local files. To turn a 3D "
        "render into a photoreal image use render_archviz: the depth map (and "
        "optionally a line map) fix the geometry, a style image sets materials and "
        "light, the prompt describes the scene. Jobs take roughly 30-120 s; if a "
        "tool returns a prompt_id instead of an image, call get_result with it."
    ),
)


# ---------------------------------------------------------------- helpers

def _path(p: str) -> Path:
    q = Path(os.path.expanduser(p))
    if not q.is_file():
        raise ValueError(f"no such file: {q}")
    return q


def _out(output_dir: str | None) -> Path:
    return Path(os.path.expanduser(output_dir)) if output_dir else OUT


def _fit(img: PILImage.Image, max_mp: float, size: tuple[int, int] | None = None) -> PILImage.Image:
    """Scale to at most max_mp megapixels with both sides multiples of 64 (SDXL),
    or to an exact size when given (so every control map matches the depth)."""
    if size is None:
        w, h = img.size
        s = min(1.0, (max_mp * 1_000_000 / (w * h)) ** 0.5)
        size = (max(64, int(w * s) // 64 * 64), max(64, int(h * s) // 64 * 64))
    return img if img.size == size else img.resize(size, PILImage.LANCZOS)


def _upload(path: str, max_mp: float | None = None, size=None) -> tuple[str, tuple[int, int]]:
    p = _path(path)
    img = PILImage.open(p)
    img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
    if max_mp is not None or size is not None:
        img = _fit(img, max_mp or 1.0, size)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    data = buf.getvalue()
    name = f"{hashlib.sha1(data).hexdigest()[:12]}_{p.stem}.png"
    r = http.post("/upload/image", files={"image": (name, data, "image/png")},
                  data={"overwrite": "true", "type": "input"})
    r.raise_for_status()
    return r.json()["name"], img.size


def _queue(graph: dict) -> str:
    r = http.post("/prompt", json={"prompt": graph, "client_id": CLIENT_ID})
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI rejected the workflow ({r.status_code}): {r.text[:3000]}")
    return r.json()["prompt_id"]


def _collect(prompt_id: str, out_dir: Path, wait: float):
    """Wait for a job, save its images; returns text + JPEG previews."""
    t0 = time.time()
    while True:
        h = http.get(f"/history/{prompt_id}").json()
        if prompt_id in h:
            break
        if time.time() - t0 > wait:
            q = http.get("/queue").json()
            pend = [j[1] for j in q.get("queue_pending", [])]
            state = ("running" if any(j[1] == prompt_id for j in q.get("queue_running", []))
                     else f"queued (position {pend.index(prompt_id) + 1})" if prompt_id in pend
                     else "not in the queue")
            return [f"Still {state} after {wait:.0f}s. prompt_id={prompt_id} - "
                    f"call get_result with it to keep waiting."]
        time.sleep(2)
    job = h[prompt_id]
    st = job.get("status", {})
    if st.get("status_str") == "error":
        errs = [m[1] for m in st.get("messages", []) if m[0] == "execution_error"]
        return [f"Job {prompt_id} failed: {json.dumps(errs)[:3000]}"]
    out_dir.mkdir(parents=True, exist_ok=True)
    saved, previews = [], []
    for node_out in job.get("outputs", {}).values():
        for im in node_out.get("images", []):
            if im.get("type") != "output":
                continue
            r = http.get("/view", params={"filename": im["filename"],
                                          "subfolder": im.get("subfolder", ""), "type": "output"})
            r.raise_for_status()
            dest = out_dir / im["filename"]
            dest.write_bytes(r.content)
            saved.append(str(dest))
            pv = PILImage.open(io.BytesIO(r.content)).convert("RGB")
            pv.thumbnail((768, 768))
            b = io.BytesIO()
            pv.save(b, "JPEG", quality=82)
            previews.append(Image(data=b.getvalue(), format="jpeg"))
    if not saved:
        return [f"Job {prompt_id} finished with no images."]
    return [f"Done in {time.time() - t0:.0f}s. Saved:\n" + "\n".join(saved), *previews]


# ---------------------------------------------------------------- tools

@mcp.tool()
def render_archviz(
    depth_path: str,
    prompt: str,
    lines_path: str | None = None,
    style_path: str | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    steps: int = 30,
    cfg: float = 5.0,
    depth_strength: float = 0.9,
    lines_strength: float = 0.8,
    style_weight: float = 0.8,
    max_megapixels: float = 1.0,
    output_dir: str | None = None,
    filename_prefix: str = "archviz",
):
    """Paint a 3D render photoreal while keeping its geometry.

    depth_path: depth map exported from the 3D model (near = bright). Sets the
      output size (scaled to max_megapixels, sides rounded to multiples of 64).
    lines_path: optional line/edge map (white lines on black) - keeps window
      frames, trim and edges crisp.
    style_path: optional style reference image - materials, palette, lighting.
    prompt / negative_prompt: describe the scene / what to avoid (the negative
      defaults to one tuned against cartoon/CGI looks and invented openings).
    depth_strength, lines_strength (0-1.5): how strictly the geometry is held.
    style_weight (0-1.5): how strongly the style image is applied.
    seed: fixed for reproducibility (random if omitted; the seed used is reported).
    Returns the saved file path(s) and a preview.
    """
    g = json.loads(ARCHVIZ.read_text())
    depth, size = _upload(depth_path, max_mp=max_megapixels)
    g["2"]["inputs"]["image"] = depth
    g["5"]["inputs"]["text"] = prompt
    if negative_prompt is not None:
        g["6"]["inputs"]["text"] = negative_prompt
    g["10"]["inputs"]["strength"] = depth_strength
    if lines_path:
        g["3"]["inputs"]["image"], _ = _upload(lines_path, size=size)
        g["11"]["inputs"]["strength"] = lines_strength
    else:  # the sampler takes its conditioning straight from the depth ControlNet
        for k in ("3", "9", "11"):
            g.pop(k)
        g["16"]["inputs"]["positive"] = ["10", 0]
        g["16"]["inputs"]["negative"] = ["10", 1]
    if style_path:
        g["4"]["inputs"]["image"], _ = _upload(style_path)
        g["13"]["inputs"]["weight"] = style_weight
    else:
        for k in ("4", "12", "13"):
            g.pop(k)
        g["16"]["inputs"]["model"] = ["1", 0]
    seed = random.randrange(2**32) if seed is None else seed
    g["16"]["inputs"].update(seed=seed, steps=steps, cfg=cfg)
    g["18"]["inputs"]["filename_prefix"] = filename_prefix
    pid = _queue(g)
    log(f"render_archviz queued {pid} {size} seed={seed}")
    res = _collect(pid, _out(output_dir), WAIT)
    res[0] = f"{res[0]}\nsize={size[0]}x{size[1]} seed={seed} prompt_id={pid}"
    return res


@mcp.tool()
def run_workflow(
    workflow_path: str,
    images: dict[str, str] | None = None,
    overrides: dict[str, dict] | None = None,
    output_dir: str | None = None,
):
    """Run any ComfyUI workflow saved in API format (Workflow > Export (API)).

    images: {node_id: local image path} - uploaded and set as that LoadImage
      node's `image`.
    overrides: {node_id: {input_name: value}}, e.g. {"16": {"seed": 7}}.
    """
    g = json.loads(_path(workflow_path).read_text())
    g = {k: v for k, v in g.items() if not k.startswith("_")}
    for nid, p in (images or {}).items():
        g[nid]["inputs"]["image"], _ = _upload(p)
    for nid, vals in (overrides or {}).items():
        g[nid]["inputs"].update(vals)
    return _collect(_queue(g), _out(output_dir), WAIT)


@mcp.tool()
def get_result(prompt_id: str, output_dir: str | None = None, wait_seconds: float | None = None):
    """Wait for (or fetch) a job queued earlier and save its images."""
    return _collect(prompt_id, _out(output_dir), WAIT if wait_seconds is None else wait_seconds)


@mcp.tool()
def comfyui_status() -> str:
    """GPU/VRAM, queue length and the models available on the server."""
    s = http.get("/system_stats").json()
    q = http.get("/queue").json()
    models = {}
    for folder in ("checkpoints", "controlnet", "ipadapter", "clip_vision", "loras"):
        r = http.get(f"/models/{folder}")
        if r.status_code == 200:
            models[folder] = r.json()
    return json.dumps({
        "url": URL,
        "devices": [{k: d.get(k) for k in ("name", "vram_total", "vram_free")}
                    for d in s.get("devices", [])],
        "comfyui": s.get("system", {}).get("comfyui_version"),
        "running": len(q.get("queue_running", [])),
        "pending": len(q.get("queue_pending", [])),
        "models": models,
        "output_dir": str(OUT),
    }, indent=1)


if __name__ == "__main__":
    mcp.run()
