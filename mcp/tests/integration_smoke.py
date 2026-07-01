"""Safe (non-mutating) integration smoke test for comfy-mcp.

Exercises every read-only + generation path against a live ComfyUI via the MCP
protocol (spawns the server over stdio). Does NOT install anything or restart
ComfyUI — those mutating paths are covered by the manual checklist in README.md.

Run:  COMFYUI_URL=http://localhost:8188 python tests/integration_smoke.py
Needs: a reachable ComfyUI with an SD1.5 checkpoint, and (for template tests)
network access to the GitHub template catalog. Exits non-zero on any failure.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

URL = os.environ.get("COMFYUI_URL", "http://localhost:8188")
CKPT = os.environ.get("TEST_CKPT", "v1-5-pruned-emaonly.safetensors")
results: list[tuple[str, bool, str]] = []


def txt(res) -> str:
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def gen_and_fetch(s, graph: dict) -> bool:
    sub = txt(await s.call_tool("submit_workflow", {"workflow": graph}))
    if "prompt_id=" not in sub:
        return False
    pid = sub.split("prompt_id=")[1].split()[0].strip()
    gr = txt(await s.call_tool("get_result", {"prompt_id": pid, "timeout_s": 180}))
    files = json.loads(gr[gr.index("["):gr.index("]") + 1])
    img = await s.call_tool("get_image", {"filename": files[0]["filename"],
          "subfolder": files[0]["subfolder"], "image_type": files[0]["type"]})
    return any(getattr(c, "type", "") == "image" for c in img.content)


async def main() -> int:
    params = StdioServerParameters(command="comfy-mcp", env={**os.environ, "COMFYUI_URL": URL})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            tools = {t.name for t in (await s.list_tools()).tools}
            check("tools registered", len(tools) >= 22, f"{len(tools)} tools")

            check("check_comfyui up", "up at" in txt(await s.call_tool("check_comfyui", {})))
            check("list_nodes keyword+display", "match" in txt(await s.call_tool("list_nodes", {"keyword": "upscale"})).lower())
            check("get_node compact", txt(await s.call_tool("get_node", {"class_name": "KSampler"})).startswith("# ") )
            check("list_models (ground truth)", "ckpt_name" in txt(await s.call_tool("list_models", {"class_name": "CheckpointLoaderSimple", "input_name": "ckpt_name"})))
            check("search_models catalog", "catalog models" in txt(await s.call_tool("search_models", {"model_type": "upscale"})))
            check("search_templates online", "catalog templates" in txt(await s.call_tool("search_templates", {"keyword": "wan"})))

            # template fetch (flowzip) + inflate round-trip
            fz = txt(await s.call_tool("get_template", {"name": "01_get_started_text_to_image", "source": "online"}))
            check("get_template flowzip", "FlowZip" in fz or "litegraph" in fz)
            check("find_missing_nodes resolves", "missing node" in txt(await s.call_tool("find_missing_nodes", {"name": "templates_hellorob_facegen_skindetail_upscale", "source": "online"})).lower())
            check("template_slots", "Overridable inputs" in txt(await s.call_tool("template_slots", {"name": "01_get_started_text_to_image", "source": "online"})))

            # error robustness
            check("bad node graceful", "No node" in txt(await s.call_tool("get_node", {"class_name": "NopeNode"})))

            # resources
            for uri in ("comfyui://object_info", "comfyui://loop-method", "comfyui://skill"):
                rr = await s.read_resource(uri)
                body = rr.contents[0].text if rr.contents else ""
                check(f"resource {uri.split('//')[1]}", len(body) > 100, f"{len(body)} chars")

            # control
            check("get_queue", "Queue" in txt(await s.call_tool("get_queue", {})))
            check("system_stats", "GB" in txt(await s.call_tool("system_stats", {})) or "cuda" in txt(await s.call_tool("system_stats", {})).lower())

            # end-to-end generation (text-to-image)
            g = {
              "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
              "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
              "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a red apple, studio photo", "clip": ["4", 1]}},
              "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["4", 1]}},
              "3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 12, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
              "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
              "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "smoke"}},
            }
            check("text2img submit->result->image", await gen_and_fetch(s, g))

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
