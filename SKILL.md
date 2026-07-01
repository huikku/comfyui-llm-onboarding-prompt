---
name: comfyui-workflows
description: Build, run, and debug ComfyUI workflows the reliable way — install ComfyUI if it's missing, query the live API (/object_info) for real node names and model files, generate API/prompt-format JSON, validate by executing (read node_errors, fix, re-POST), then iterate on the rendered output (view it, tweak params, re-run until it looks right). Use whenever generating, running, converting, or fixing a ComfyUI graph or node setup. ComfyUI defaults to http://localhost:8188.
---

# ComfyUI workflow generation

ComfyUI exposes a REST API that tells you exactly which nodes are installed, their inputs/outputs, and which model files exist. **Always query the API before generating workflow JSON, and validate by executing before declaring a workflow done.** Never guess node names, input names, types, or model filenames.

ComfyUI runs at `http://localhost:8188` unless told otherwise.

## Step 0 — Pick the JSON format deliberately

Two formats, NOT interchangeable:

| | **API / prompt format** | **UI / litegraph format** |
|---|---|---|
| Shape | flat dict keyed by node id; each entry is `class_type` + `inputs` | `nodes` + `links` arrays, positions, `widgets_values` |
| Used for | POSTing to `/prompt` to **execute** | loading into the **UI** to view/edit |
| Generate when | "build it and run it" (default) | an artist needs to hand-tweak the graph |

**Default to API format.** It's robust to generate (no link-id accounting). Produce litegraph only when the goal is a UI-editable file. To run an existing *UI* workflow: convert it to API format (resolve reroute/GetNode/SetNode passthroughs and turn `widgets_values` into named `inputs` using `object_info`), validate in API format, then optionally save litegraph.

## Install ComfyUI (only if it isn't already running)
Skip this whenever Step 1 succeeds. If nothing is listening and ComfyUI isn't on the box:
```bash
git clone https://github.com/comfyanonymous/ComfyUI && cd ComfyUI
python3 -m venv .venv && . .venv/bin/activate
# NVIDIA / CUDA 12.x. For CPU-only, AMD/ROCm, or Apple Silicon see the repo README.
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
python main.py --listen 0.0.0.0 --port 8188      # add --cpu if there's no GPU
```
- **Custom nodes:** install ComfyUI-Manager once, then manage packs through it — or `git clone` each pack into `custom_nodes/` and `pip install -r` its requirements. **Restart ComfyUI** so `/object_info` reflects new nodes.
  ```bash
  git clone https://github.com/Comfy-Org/ComfyUI-Manager custom_nodes/ComfyUI-Manager
  ```
- **Models** live under `models/<type>/` (`checkpoints`, `loras`, `vae`, `clip`, …). ComfyUI only offers files actually on disk (Step 3).
- One-liner alternative: `pip install comfy-cli && comfy install`, then `comfy launch`.

## Step 1 — Confirm ComfyUI is up
```bash
curl -s http://localhost:8188/object_info | head -c 100
```
Nothing back ⇒ not running (or another port). Check the host/port; install only if it's genuinely absent (above).

## Step 2 — Discover nodes
```bash
curl -s http://localhost:8188/object_info/NodeClassName      # one node, fast — exact interface
# keyword search:
curl -s http://localhost:8188/object_info | python3 -c "
import json,sys
n=json.load(sys.stdin)
for k in sorted(n):
    if 'mask' in k.lower(): print(k,'->',n[k].get('display_name',k))"
```
Each entry has `input.required`/`input.optional` (type, default, min/max, tooltip), `output` (e.g. `[\"IMAGE\",\"MASK\"]`), `output_name`, `display_name`, `category`.

## Step 3 — Discover real model files (no hallucinated checkpoints/LoRAs)
Loader nodes embed valid file choices as an **enum**. There are **two encodings** in the wild — handle both:
- **Legacy:** the input's type is a list → element 0 is the allowed values (e.g. `ckpt_name[0] == ['model.safetensors', ...]`).
- **Newer:** the type is the string `"COMBO"` and element 1 is `{"options": [...], ...}` → read `[1]["options"]`.

Same node graph can mix both (core checkpoint/sampler enums are legacy; some packs emit `COMBO`). A robust extractor:
```bash
curl -s http://localhost:8188/object_info/UpscaleModelLoader | python3 -c "
import json,sys
spec=json.load(sys.stdin)['UpscaleModelLoader']['input']['required']['model_name']
print(spec[1]['options'] if spec[0]=='COMBO' else spec[0])"
```
Do this for every loader (checkpoints, LoRAs, VAEs, controlnets, SAM/face/detection models, text encoders). If the file you want isn't in the enum, say so — never invent a filename.

## Step 4 — Generate (API format)
Flat dict. Each node: string id key, `class_type`, `inputs`. An input is a literal or a link `["source_node_id", output_index]`.
```json
{
  "4": { "class_type": "CheckpointLoaderSimple", "inputs": { "ckpt_name": "model.safetensors" } },
  "6": { "class_type": "CLIPTextEncode", "inputs": { "text": "a portrait", "clip": ["4", 1] } },
  "3": { "class_type": "KSampler", "inputs": {
      "seed": 42, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
      "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0] } }
}
```
`["4", 1]` = output index 1 of node `4`. Use exact input names and output indices from `object_info`. Every required input must be satisfied.

## Step 5 — Execute and validate (do NOT skip)
```bash
curl -s -X POST http://localhost:8188/prompt -H "Content-Type: application/json" \
  -d '{"prompt": { ...API-format dict... }, "client_id": "llm-gen"}'
```
- **Success** ⇒ response has `prompt_id`. Poll: `GET /history/<prompt_id>` (output filenames), then `GET /view?filename=&subfolder=&type=output`.
- **Failure** ⇒ HTTP 400 with `error` + a `node_errors` map keyed by node id. **Read `node_errors`, fix that node, re-POST.** This generate→execute→parse-error→fix loop is the whole point. Never hand back an unvalidated graph.

Upload an input image first: `curl -s -X POST http://localhost:8188/upload/image -F "image=@/path/to/input.png"`. (Videos: VHS_LoadVideo* read from ComfyUI's `input/` dir — place the file there.)

## Step 6 — Iterate on the rendered output (validate by looking)
A graph with zero `node_errors` is **valid, not correct**. Mangled hands, a drifted background, an over-strong effect, a hard matte seam — none of that surfaces in `node_errors`, only in the pixels. After it runs, **look at the output, judge it against the intent, change parameters, and re-run until it looks right.**

Make each iteration **one command** — a small script that submits, polls, fetches, and surfaces a frame to view:
```bash
pid=$(curl -s -X POST localhost:8188/prompt -H 'Content-Type: application/json' \
      -d "{\"prompt\": $(cat graph.json), \"client_id\":\"iter\"}" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["prompt_id"])')
# poll GET /history/$pid until present → grab the output filename → fetch it:
#   curl -s "localhost:8188/view?filename=<f>&subfolder=<s>&type=output" -o out.png
# video output: pull a representative frame to actually look at
ffmpeg -y -v error -i out.mp4 -vf 'select=eq(n\,12)' -vframes 1 frame.png
```
Then **view the frame** (read the image) and decide. Re-run the same script after each tweak.

**Comparisons that make problems obvious:**
- **Side-by-side** original vs result: `ffmpeg -i a.png -i b.png -filter_complex hstack out.png`.
- **Difference over 50% gray** (`0.5 + 0.5*(a-b)` per pixel): identical regions read flat gray, only real changes pop — the fastest way to confirm "the background didn't change" or to spot a seam.

Tune the **parameters**, not just the wiring: resolution, strength/denoise, mask grow/feather, steps/cfg, seed. (Rig-specific knobs — the OOM ladder, mask-grows-with-resolution, background-preservation comps — belong in project notes/memory, not in this general loop.)

## Step 7 (optional) — UI-editable litegraph
Only if a human needs to edit it. Save to `user/default/workflows/<name>.json`. Gotchas: every link is `[link_id, from_node, from_slot, to_node, to_slot, type]` in the top-level `links` AND referenced in source `outputs[].links` + target `inputs[].link` (unique, consistent; `last_node_id`/`last_link_id` ≥ max used). `widgets_values` is **positional** in widget order, excludes wired inputs, and seed nodes inject an extra `control_after_generate`. When unsure: build in API format, run it, mirror the resolved values.

## Useful endpoints
`GET /object_info[/{node}]` (specs + model enums) · `POST /prompt` (queue+validate) · `GET /history/{id}` (results) · `GET /view?filename=&subfolder=&type=` (fetch output) · `POST /upload/image` · `GET /queue` · `POST /interrupt` · `GET /system_stats` (VRAM/device).

## Prefer adapting known-good graphs
Knowing a node's interface ≠ knowing a correct wiring. Start from a working example and use `object_info` to validate/adapt. Few-shot from a real graph beats zero-shot from specs. Where to get known-good graphs:
- **The template catalog on your install** — `GET /api/workflow_templates` lists every template shipped by each installed node pack; fetch one with `GET /api/workflow_templates/<pack>/<name>.json`.
- **The official online catalog** — the open `Comfy-Org/workflow_templates` repo (~550 workflows): index at `https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/index.json`, each graph at `.../templates/<name>.json`. No install needed to browse. An online template may reference nodes/models you don't have — reconcile against `object_info` and install as needed.
- A node pack's `example_workflows/`, or any saved graph.

These are **UI / litegraph** format — convert to API format (resolve passthroughs, `widgets_values` → named inputs via `object_info`) before executing.

## Common failures
- Wrong node names (case-sensitive; differ from display names) — verify via API.
- Wrong input names/indices — match `object_info` exactly.
- Hallucinated model files — pick from the loader enum (Step 3).
- Type mismatches — exact (`SAM_MODEL`→`SAM_MODEL`); only `*` is flexible.
- Mixed formats — don't POST litegraph to `/prompt`; don't load API format into the UI.
- **Stale API** — if custom nodes were just installed/changed, ComfyUI needs a **restart** before `/object_info` reflects them.

**Rules of thumb: query the API first. Generate API format by default. Validate by executing. Never guess a name, type, or filename.**
