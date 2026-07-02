# comfy-mcp tests

Integration tests — they drive the MCP server against a **live ComfyUI**. There
are no pure unit tests; the value is in exercising the real API.

## Prerequisites

- `pip install -e .` (installs `comfy-mcp` + deps)
- A reachable ComfyUI with an SD1.5 checkpoint (set `TEST_CKPT` to override the
  default `v1-5-pruned-emaonly.safetensors`).
- Network access to the GitHub template catalog (for template/bench tests).
- `COMFYUI_URL` pointed at your ComfyUI (default `http://localhost:8188`; for a
  remote box, open an SSH tunnel first — see the main README).

## Safe suites (no mutations)

```bash
COMFYUI_URL=http://localhost:8188 python tests/integration_smoke.py   # exits non-zero on any failure
COMFYUI_URL=http://localhost:8188 python tests/bench.py               # compression/conversion metrics
```

`integration_smoke.py` covers (17 checks): tool registration, `check_comfyui`,
`list_nodes` (compact), `get_node`, `list_models`, `search_models`,
`search_templates`, `get_template` (flowzip), `find_missing_nodes`,
`template_slots`, error robustness, all three resources, `get_queue`,
`system_stats`, and a full text-to-image submit→result→get_image. It does
**not** install anything or restart ComfyUI.

## Mutating paths — manual, and verified once on a live box

These change the ComfyUI host (install code/files, restart the server), so they
are **not** in the automated suite — run them deliberately. Verified end-to-end
on linuxdev (RTX 4090, ComfyUI 0.25.0, ComfyUI-Manager V3.41):

| Path | Result | Notes |
|---|---|---|
| `install_model("RealESRGAN x2")` → `list_models` | ✅ PASS | 67 MB; appeared in `UpscaleModelLoader` enum; no restart needed |
| `restart_comfyui` → `check_comfyui` | ✅ PASS | ~13 s recovery (ComfyUI under pm2 auto-restarts) |
| `install_node_pack("rgthree-comfy")` → `restart_comfyui` → `list_nodes` | ✅ PASS | 24 `(rgthree)` nodes registered after restart |

**Bug found + fixed during this pass:** `install_node_pack` returned HTTP 500
because Manager's `/manager/queue/install` reads `channel`/`mode` by direct key
access; those are now sent (`channel="default"`, `mode="cache"`). Without live
testing this would have shipped broken.

To re-run a mutating check manually (installs a real pack — do it deliberately):

```python
# python - <<'PY'  (with COMFYUI_URL set)
import asyncio, os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    p = StdioServerParameters(command="comfy-mcp", env={**os.environ})
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print(await s.call_tool("install_node_pack", {"pack_id": "rgthree-comfy"}))
            print(await s.call_tool("restart_comfyui", {}))
asyncio.run(main())
# PY
```
