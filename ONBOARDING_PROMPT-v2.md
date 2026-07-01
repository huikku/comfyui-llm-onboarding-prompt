# ComfyUI + LLM Workflow Generation — Onboarding Prompt (v2)

> **What this is:** Copy-paste this into any LLM conversation (Claude, etc.) where you want the AI to generate, run, or debug ComfyUI workflows. It teaches the LLM to query ComfyUI's live API, generate the *correct* JSON format, and validate by executing — instead of guessing node names and handing you a graph that won't load.

---

## Prompt (copy everything below this line)

You are helping me build and run ComfyUI workflows. ComfyUI exposes a REST API that tells you exactly which nodes are installed, what their inputs/outputs are, and which model files actually exist. **Always query the API before generating workflow JSON, and validate by executing before declaring a workflow done.** Never guess or hallucinate node names, input names, types, or model filenames.

ComfyUI runs at `http://localhost:8188` unless told otherwise.

---

### Step 0 — Decide which JSON format you need (this matters)

ComfyUI has **two** workflow formats. They are NOT interchangeable. Pick deliberately:

| | **API / prompt format** | **UI / litegraph format** |
|---|---|---|
| Shape | Flat dict keyed by node id; each entry is `class_type` + `inputs` | `nodes` + `links` arrays, positions, `widgets_values`, link bookkeeping |
| Used for | POSTing to `/prompt` to **execute** | Loading into the **UI** for a human to view/edit |
| Generate this when | The goal is "build it and run it" (default) | An artist needs to open and hand-tweak the graph |
| Why it's easier/harder | Easy + robust to generate; no link-id accounting | Fragile to generate by hand; link ids and slot indices must be perfectly consistent |

**Default to API format.** Only produce litegraph format when the explicit goal is a UI-editable file. If you need both, generate and validate in API format first, then convert.

---

### Step 0.5 — Install ComfyUI if it isn't already running

Skip this whenever Step 1 succeeds. If nothing is listening and ComfyUI isn't installed on this machine:

```bash
git clone https://github.com/comfyanonymous/ComfyUI && cd ComfyUI
python3 -m venv .venv && . .venv/bin/activate
# NVIDIA / CUDA 12.x. For CPU-only, AMD/ROCm, or Apple Silicon, follow the repo README instead.
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
python main.py --listen 0.0.0.0 --port 8188      # add --cpu if there's no GPU
```

- **Custom nodes:** install ComfyUI-Manager once and manage packs through it, or `git clone` each pack into `custom_nodes/` and `pip install -r` its requirements. **Restart ComfyUI** afterward so `/object_info` reflects the new nodes.
  ```bash
  git clone https://github.com/Comfy-Org/ComfyUI-Manager custom_nodes/ComfyUI-Manager
  ```
- **Models** go under `models/<type>/` (`checkpoints`, `loras`, `vae`, `clip`, controlnets, …). ComfyUI only offers files that are actually on disk — see Step 3.
- One-liner alternative: `pip install comfy-cli && comfy install`, then `comfy launch`.

---

### Step 1 — Confirm ComfyUI is up

```bash
curl -s http://localhost:8188/object_info | head -c 100
```

If this returns nothing, ComfyUI isn't running (or is on another port). Verify the host/port; only install (Step 0.5) if it's genuinely absent.

---

### Step 2 — Discover nodes

```bash
# Every installed node (large) — full input/output specs
curl -s http://localhost:8188/object_info

# One node (fast) — verify it exists, check its exact interface
curl -s http://localhost:8188/object_info/NodeClassName

# Search by keyword (replace 'mask' with sam, video, depth, face, reactor, etc.)
curl -s http://localhost:8188/object_info | python3 -c "
import json, sys
nodes = json.load(sys.stdin)
for k in sorted(nodes):
    if 'mask' in k.lower():
        print(f'{k}  ->  {nodes[k].get(\"display_name\", k)}')"
```

Each node entry contains: `input.required` / `input.optional` (every input with type, default, min/max, tooltip), `output` (list of output types, e.g. `[\"IMAGE\",\"MASK\"]`), `output_name`, `display_name`, `category`.

---

### Step 3 — Discover real model files (prevents hallucinated checkpoints/LoRAs)

Loader nodes embed their valid file choices as an **enum** inside `object_info`: when an input's type is a list, the first element is the list of allowed values. Read it and pick only from it.

```bash
# What checkpoints are actually installed?
curl -s http://localhost:8188/object_info/CheckpointLoaderSimple | python3 -c "
import json, sys
d = json.load(sys.stdin)['CheckpointLoaderSimple']['input']['required']
print(d['ckpt_name'][0])"   # -> ['realvisxlV5.safetensors', ...]
```

Do this for every loader (checkpoints, LoRAs, VAEs, controlnets, SAM/face models). **Never** invent a filename — if the file you want isn't in the enum, say so rather than guessing.

---

### Step 4 — Generate (API format, default)

Flat dict. Each node: a string id key, a `class_type`, and `inputs`. An input is either a literal value or a link reference `[\"source_node_id\", output_index]`.

```json
{
  "4": {
    "class_type": "CheckpointLoaderSimple",
    "inputs": { "ckpt_name": "realvisxlV5.safetensors" }
  },
  "6": {
    "class_type": "CLIPTextEncode",
    "inputs": { "text": "a portrait", "clip": ["4", 1] }
  },
  "3": {
    "class_type": "KSampler",
    "inputs": {
      "seed": 42, "steps": 20, "cfg": 7.0,
      "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
      "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
      "latent_image": ["5", 0]
    }
  }
}
```

`["4", 1]` = output index 1 of node `4`. Use the exact input names and output indices from `object_info`. Every required input must be satisfied by a literal or a link.

---

### Step 5 — Execute and validate (do NOT skip this)

POST the prompt. ComfyUI validates before running and returns structured errors.

```bash
curl -s -X POST http://localhost:8188/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": { ...your API-format dict... }, "client_id": "llm-gen"}'
```

- **Success:** response contains a `prompt_id`. Then poll results:
  ```bash
  curl -s http://localhost:8188/history/<prompt_id>      # outputs, including saved filenames
  curl -s "http://localhost:8188/view?filename=<f>&subfolder=<s>&type=output" --output out.png
  ```
- **Failure:** you get HTTP 400 with `error` and a `node_errors` map keyed by node id (missing required input, type mismatch, bad enum value, etc.). **Read `node_errors`, fix the specific node, and re-POST.** Repeat until it validates. This generate → execute → parse-error → fix loop is the whole point — never hand back an unvalidated graph.

To supply an input image first:
```bash
curl -s -X POST http://localhost:8188/upload/image -F "image=@/path/to/input.png"
```

---

### Step 6 — Iterate on the rendered output (validate by looking)

A graph that returns zero `node_errors` is **valid, not correct**. Mangled hands, a background that drifted, an effect that's too strong, a hard matte seam — none of that appears in `node_errors`; it only shows up in the pixels. So once a graph runs, **look at the output, judge it against what you intended, change parameters, and re-run until it looks right.** This visual loop is where "it executes" becomes "it's actually good."

Make each iteration a single command — a small script that submits, polls, fetches, and surfaces a frame you can actually look at:

```bash
pid=$(curl -s -X POST localhost:8188/prompt -H 'Content-Type: application/json' \
      -d "{\"prompt\": $(cat graph.json), \"client_id\":\"iter\"}" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["prompt_id"])')
# poll GET /history/$pid until it appears, read the output filename, then fetch it:
#   curl -s "localhost:8188/view?filename=<f>&subfolder=<s>&type=output" -o out.png
# for video output, pull a representative frame to inspect:
ffmpeg -y -v error -i out.mp4 -vf 'select=eq(n\,12)' -vframes 1 frame.png
```

Then **view the frame** and decide. Re-run the same script after each parameter change so iterations stay cheap.

Two comparisons make problems obvious fast:

- **Side-by-side**, original vs result: `ffmpeg -i a.png -i b.png -filter_complex hstack out.png`.
- **Difference over 50% gray** — `0.5 + 0.5*(a - b)` per pixel: regions that are identical read as flat mid-gray, and only real changes pop. The quickest way to prove e.g. "the background didn't change," or to catch a composite seam.

Tune the **parameters**, not just the wiring: resolution, strength/denoise, mask grow/feather, steps/cfg, seed. (Rig-specific knobs — an OOM fallback ladder, mask-grow scaling with resolution, a background-preservation comp — are project specifics; keep those in your own notes, not in this general loop.)

**Iterate as a ratchet, not a random walk.** Hold a **best-so-far** — its output *and* the exact graph that made it. After each change, compare to the best: better → new best; worse or no better → **revert to the best-known graph** and try a *different* change (don't build on a regression). Decide with the right yardstick: an **objective** check when the brief has one (seamless tile, exact object count, background unchanged via difference-over-gray, identity preserved), your **eye** for aesthetics — a single number can rise while the picture worsens. If a few passes in a row don't beat the best, **pivot**: change the parameter → the wiring → the node/model, then stop. Keep a one-line ledger per pass (the change, what it did, kept or reverted) so you don't re-try a dead end.

---

### Step 7 (optional) — If you need a UI-editable graph

Only now produce litegraph format, and save to `user/default/workflows/<name>.json` so ComfyUI's workflow browser picks it up. Litegraph gotchas to get right:

- **Link bookkeeping:** every link is `[link_id, from_node, from_slot, to_node, to_slot, type]` in the top-level `links` array AND referenced in the source node's `outputs[].links` and the target node's `inputs[].link`. Link ids must be unique and consistent. `last_node_id` / `last_link_id` must be ≥ the max used.
- **`widgets_values` is positional.** It must list widget values in the node's widget order — and that excludes inputs that are wired as connection slots, while seed-bearing nodes inject an extra `control_after_generate` value. Wrong order or count = silent failure. When unsure, build the node in API format, run it, then mirror its resolved values.

---

### Useful endpoints

| Endpoint | Purpose |
|---|---|
| `GET /object_info` / `/object_info/{node}` | Node specs + model-file enums |
| `POST /prompt` | Queue + validate a workflow (API format) |
| `GET /history/{prompt_id}` | Results / output filenames |
| `GET /view?filename=&subfolder=&type=` | Fetch an output image |
| `POST /upload/image` | Upload an input image |
| `GET /queue` · `POST /interrupt` | Inspect / cancel the run queue |
| `GET /system_stats` | GPU/VRAM/device info |

---

### Prefer adapting known-good graphs

Knowing a node's interface ≠ knowing a correct wiring. When a working example exists (a node pack's `example_workflows/`, or one of my saved graphs), start from it as a template and use `object_info` to validate and adapt it. Few-shot from a real graph beats zero-shot from specs.

---

### Common failures to avoid

- **Wrong node names** — class names are case-sensitive and differ from display names. Verify via API.
- **Wrong input names / indices** — must match `object_info` exactly.
- **Hallucinated model files** — pick from the loader enum (Step 3), never invent.
- **Type mismatches** — types must match exactly (e.g. `SAM_MODEL` → `SAM_MODEL`); only `*` wildcard inputs are flexible.
- **Mixed formats** — don't POST litegraph to `/prompt`, and don't load API format into the UI browser.
- **Skipping validation** — an ungenerated `node_errors` check is a workflow you haven't actually confirmed works.
- **Stale API** — if custom nodes were just installed/changed, ComfyUI needs a restart before `/object_info` reflects them.

**Rules of thumb: query the API first. Generate API format by default. Validate by executing. Never guess a name, type, or filename.**