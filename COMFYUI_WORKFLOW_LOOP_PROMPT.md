# ComfyUI Workflow Loop — Autonomous Build-and-Iterate Prompt

> **What this is:** A **self-contained** prompt. Copy-paste it (everything below the line) into an LLM that has shell access to a running ComfyUI. It covers *both* halves of the job — **building** the workflow correctly off the live API, and **looping** on the result (run → look → critique → change → run again) until the output actually matches what you asked for. The first version that merely executes is the *start* of the work, not the end.
>
> You don't need any other file. (The repo's `ONBOARDING_PROMPT-v2.md` is a lighter, build-only version of the same mechanics — this prompt supersets it with the loop.)

---

## Prompt (copy everything below this line)

You are building a ComfyUI workflow to achieve a goal I will give you. **Your job is not to deliver a graph — it is to deliver a good result.** You reach it by looping: build → run → look → critique → change → run again, repeating until the output matches the intent. Do not stop at the first version that simply executes without errors.

ComfyUI runs at `http://localhost:8188` unless I say otherwise. Never guess or hallucinate node names, input names, types, or model filenames — get the truth from the API first.

**Which format you're building (tell me up front).** By default you build the **API / prompt format** — the flat, runnable graph the loop POSTs to `/prompt`. That is mandatory for the loop and is **not** the file I drag onto the ComfyUI canvas. If I want a **UI / litegraph `workflow.json`** to open and hand-edit, I'll ask — then build and validate in API format first and convert at the end. State at the start that you're delivering the runnable API graph, and remind me I can request the UI version anytime.

---

### Set up and build correctly (discover, don't guess)

**Confirm ComfyUI is up.**
```bash
curl -s http://localhost:8188/object_info | head -c 100
```
Nothing back ⇒ it isn't running (or it's on another port). If it's genuinely absent, install + start it:
```bash
git clone https://github.com/comfyanonymous/ComfyUI && cd ComfyUI
python3 -m venv .venv && . .venv/bin/activate
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124  # CPU/AMD/Apple: see repo README
pip install -r requirements.txt
python main.py --listen 0.0.0.0 --port 8188   # add --cpu if no GPU
```

**Discover the real nodes.**
```bash
curl -s http://localhost:8188/object_info/<NodeClass>          # one node — exact inputs/outputs
curl -s http://localhost:8188/object_info | python3 -c "       # keyword search (swap 'mask')
import json,sys; n=json.load(sys.stdin)
[print(k,'->',n[k].get('display_name',k)) for k in sorted(n) if 'mask' in k.lower()]"
```
Each entry has `input.required` / `input.optional` (type, default, min/max, tooltip), `output` types (e.g. `[\"IMAGE\",\"MASK\"]`), `display_name`, `category`.

**Discover real model files.** A loader's valid files are an **enum** inside `object_info` — the first element of a list-typed input. Pick only from there; never invent a filename.
```bash
curl -s http://localhost:8188/object_info/CheckpointLoaderSimple | python3 -c "
import json,sys; print(json.load(sys.stdin)['CheckpointLoaderSimple']['input']['required']['ckpt_name'][0])"
```
Do this for every loader (checkpoints, LoRAs, VAEs, controlnets, SAM/face/upscale models).

**Generate API / prompt format.** A flat dict: each node is a string id → `class_type` + `inputs`. An input is a literal, or a link `["source_node_id", output_index]`. Every required input must be satisfied.
```json
{
  "4": { "class_type": "CheckpointLoaderSimple", "inputs": { "ckpt_name": "<from-enum>.safetensors" } },
  "6": { "class_type": "CLIPTextEncode", "inputs": { "text": "a portrait", "clip": ["4", 1] } },
  "3": { "class_type": "KSampler", "inputs": {
      "seed": 42, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
      "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0] } }
}
```
Upload an input image first when needed: `curl -s -X POST http://localhost:8188/upload/image -F "image=@/path/in.png"`. (Video nodes like `VHS_LoadVideo*` read files placed in ComfyUI's `input/` dir.)

**You're allowed to extend the environment to get the job done:**
- **Install what you need.** If no installed node does the job, install the pack from a **trusted source** (ComfyUI-Manager, or the official / maintainer GitHub repo) into `custom_nodes/`, `pip install -r` its requirements, then **restart ComfyUI and re-query `/object_info`** so the new nodes register. Download missing model files into the correct `models/<type>/` folder. Don't install from sketchy / unvetted sources.
- **Write a custom node when nothing fits.** Author a small Python node in `custom_nodes/` (`INPUT_TYPES` / `RETURN_TYPES` / `FUNCTION` / `NODE_CLASS_MAPPINGS`), restart, and use it — for a bespoke composite, a math/masking op, or glue between two nodes. Prefer an existing node first; reach for custom when the task genuinely needs it.

**Adapt known-good graphs.** Few-shot from a working example beats zero-shot from specs. Sources:
- **Template catalog on your install:** `GET /api/workflow_templates` (index), then `GET /api/workflow_templates/<pack>/<name>.json`.
- **Official online catalog** (~550 workflows, no install to browse): index at `https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/index.json`, each graph at `.../templates/<name>.json`. An online template may need nodes/models you lack — reconcile against `object_info` and install as needed.
- A node pack's `example_workflows/`, or a saved graph.

All of these are **UI / litegraph** format — convert to API format (resolve passthroughs, `widgets_values` → named inputs via `object_info`) before you POST to `/prompt`.

---

### THE LOOP — repeat until the result meets the brief

Run this cycle **autonomously**. After each pass, **show me the result** (see *Showing results*) and one line on what you changed, then start the next pass — you do **not** need to check in between fix-passes. Keep iterating on your own as long as you can name a **real defect** to fix; don't hand me the first version that merely runs. **The moment you judge the output actually meets the brief, stop, present it, and ask me for feedback** — don't keep looping silently, and don't invent pointless variations to avoid stopping. I'll either sign off (done) or tell you what to change, and you resume from there. (If I explicitly ask you to keep exploring variations, do — otherwise that convergence point is where you pause for me.)

**1 · BUILD / ADJUST.** Generate the API-format graph, or change exactly **one** thing on the current one (see *What to change*). One change per pass — otherwise you can't tell what helped.

**2 · RUN.** POST to `/prompt`. If you get `node_errors`, that is *not* an iteration — read the error, fix that specific node, re-POST until the graph actually executes. (Inner loop; never count an un-run graph as progress.)
```bash
curl -s -X POST http://localhost:8188/prompt -H 'Content-Type: application/json' \
  -d '{"prompt": { ...API-format dict... }, "client_id": "loop"}'
# success -> response has "prompt_id"; failure -> HTTP 400 with "node_errors" keyed by node id. Read it, fix, re-POST.
```

**3 · LOOK.** Fetch the output and *actually view it*. A graph with zero `node_errors` is **valid, not correct** — mangled hands, a drifted background, a hard matte seam, an over-strong effect, the wrong framing: none of that shows in `node_errors`, only in the pixels. Keep each pass to one command:
```bash
pid=$(curl -s -X POST localhost:8188/prompt -H 'Content-Type: application/json' \
      -d "{\"prompt\": $(cat graph.json), \"client_id\":\"loop\"}" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["prompt_id"])')
# poll GET /history/$pid until present -> read the output filename -> fetch it:
#   curl -s "localhost:8188/view?filename=<f>&subfolder=<s>&type=output" -o out.png
# video output: pull a representative frame to inspect
ffmpeg -y -v error -i out.mp4 -vf 'select=eq(n\,12)' -vframes 1 frame.png
```
Two comparisons make defects jump out: **side-by-side** (`ffmpeg -i a.png -i b.png -filter_complex hstack out.png`) and **difference over 50% gray** (`0.5 + 0.5*(a-b)` — identical areas read flat gray, only real changes pop).

**4 · CRITIQUE.** State, concretely, how this output differs from the intent. Not "looks fine" — name the specific defect: *"six fingers," "relight too warm on the left," "hard matte edge along the jaw," "upscale softened the eyes," "the comp shifted the background," "the face flickers between frames."* If you genuinely can't name a defect, the requirements are met.

**5 · DECIDE.** Can you still name a concrete defect? → pick the **one** change that fixes it, apply the ratchet below, and go back to step 1, no need to check in. Can you **not** name a defect — the brief's requirements are met? → **stop, present the result, and ask me for feedback.** Don't keep iterating past that point on your own.

---

### Keep-best, revert, and pivot — the ratchet that makes it converge

One change per pass isn't enough on its own — you also have to refuse to build on regressions. Run the loop as a **ratchet** so progress only moves one direction:

- **Hold a best-so-far.** Save each pass's output *and the exact graph that produced it*. The best result seen so far is your baseline.
- **Keep or revert, every pass.** Compare the new output to the best. Better → it becomes the new best. Worse or no better → **revert to the best-known graph** and try a *different* change. Never keep iterating on top of a change that made things worse.
- **Judge by the right yardstick.** Where the brief has an *objective* test — does it tile with no seam? exact object count? background unchanged (difference-over-gray)? identity preserved? — gate keep/revert on that measurable check. Where it's *aesthetic*, your **eye** is the judge, not a single number: a metric can climb while the picture gets worse (a busier background can raise a "sharpness" score while the subject softens). Objective gate where one exists; look everywhere else.
- **Pivot on plateau.** If several passes in a row don't beat the best, escalate instead of nudging the same knob: (1) change *which* parameter or strategy → (2) change the *wiring / node / model* (different sampler family, add a control or identity node, swap the checkpoint) → (3) still stuck → stop and present the best-so-far with what you tried. Don't grind a dead knob.

Keep a **loop ledger** as you go — one line per pass: the **hypothesis** (the single change and why), the **observation** (what it did to the output), the **verdict** (new best / reverted). It makes the run auditable and stops you re-trying something you already rejected.

---

### Showing results — surface every pass

Don't run silently. Every pass, put the output in front of me:
- **Always** surface the image inline if you can render images; otherwise save it to a predictable path (e.g. `out_pass03.png`) and tell me. This works in every environment.
- **Optionally**, if a desktop display is available and I've asked to watch live, also open it in an image viewer — but launch it **detached** so it never blocks the loop (e.g. `setsid <viewer> out.png >/dev/null 2>&1 &`), and **reuse a single window / fixed filename** (or close the previous viewer) so a long run doesn't pile up windows.
- In a **headless / remote / no-display** environment, do **not** try to open a GUI viewer — it'll fail or hang. Just surface inline or by path.

### What to change (tune parameters, not just wiring)

`denoise` · `steps` · `cfg` · `seed` · sampler / scheduler · mask grow / feather · resolution / tile size · prompt wording · model choice. Match the change to the critique:
- soft / lacking detail → raise denoise, or use a stronger upscaler
- identity / structure drifted → lower denoise, or add an identity / control node
- hard matte edge → grow + feather the mask
- effect too strong → lower its weight / blend
- temporal flicker (video) → add a stabilizer/smoothing node, or lock the seed across frames
- wrong framing or content → fix the prompt or the conditioning, not the sampler

### The convergence checkpoint (present, then ask)

You don't stop after the first version that runs — you keep fixing autonomously while a real defect remains. But the loop isn't infinite: **when the output meets the brief, pause and hand it to me** —
1. the final **output**,
2. the **graph** — API / runnable format, plus a **UI / litegraph `workflow.json`** only if I asked for one, and
3. the running **loop ledger** — one line per pass: the change (hypothesis), what it did (observation), and whether it was kept as the new best or reverted —

and **ask whether it's approved or needs changes.** If I approve, you're done. If I want changes, resume the loop from there. I can also stop you at any time. Keep the ledger running the whole way so it's ready the instant you reach the checkpoint.

---

### Useful endpoints

| Endpoint | Purpose |
|---|---|
| `GET /object_info[/{node}]` | node specs + model-file enums |
| `POST /prompt` | queue + validate a workflow (API format) |
| `GET /history/{prompt_id}` | results / output filenames |
| `GET /view?filename=&subfolder=&type=` | fetch an output |
| `POST /upload/image` | upload an input image |
| `GET /queue` · `POST /interrupt` | inspect / cancel the run queue |
| `GET /system_stats` | GPU / VRAM / device info |

### Common failures to avoid

- **Wrong node names** — class names are case-sensitive and differ from display names; verify via API.
- **Wrong input names / indices** — must match `object_info` exactly.
- **Hallucinated model files** — pick from the loader enum, never invent.
- **Type mismatches** — exact (`SAM_MODEL`→`SAM_MODEL`); only `*` wildcard inputs are flexible.
- **Mixed formats** — don't POST litegraph to `/prompt`, don't load API format into the UI.
- **Stale API** — after installing/changing custom nodes, **restart ComfyUI** before `/object_info` reflects them.

### Principles

- Validate by **looking**, never by a green "it ran."
- **One change per pass** — so you always know what helped.
- **Ratchet:** hold a best-so-far; keep a change only if it beats it, else revert and try something different. Pivot (param → wiring → model) when passes plateau.
- Keep each pass to **one command** — cheap iterations mean more of them.
- **Save every pass's output** — you'll want to compare them (and to revert to the best).
- Tune **parameters**, not just wiring. Most "it's almost right" problems are a knob, not a rewire.
- On heavy jobs (video, big batches) **iterate on a short range / a few frames, and commit the full run only once it's dialed in.**
