# comfy-mcp — a loop-aware MCP server for ComfyUI

An [MCP](https://modelcontextprotocol.io) server that wraps a **local** ComfyUI
install and bakes in the build→run→**look**→critique→fix loop from this repo.
It doesn't just call the API — every tool description and response nudges the
model through the loop: discover real nodes/models before building, validate by
executing, and *actually look at the pixels* before deciding a graph is done.
A graph that runs with zero `node_errors` is **valid, not correct**.

> Complements ComfyUI's official **Comfy Cloud MCP** (`cloud.comfy.org/mcp`),
> which runs workflows on Comfy Cloud GPUs. This one points at *your own*
> ComfyUI (`http://localhost:8188`) and is single-sourced with the loop prompt.

## The three MCP primitives, mapped to the loop

| Primitive | What it exposes | Loop step |
|---|---|---|
| **Tools** | `check_comfyui`, `list_nodes`, `get_node`, `list_models` | Discover, don't guess |
| | `upload_image`, `submit_workflow` | Build → Run |
| | `get_result`, `get_image` (returns the actual image) | **Look** |
| | `system_stats`, `get_queue`, `interrupt` | Control |
| **Prompts** | `comfy_loop` (full method), `comfy_skill` (compact) | The whole discipline, one command |
| **Resources** | `comfyui://object_info` (live), `comfyui://loop-method`, `comfyui://skill` | Truth + docs |

`get_image` returns the rendered output *to the model* — that's the step that
makes the loop real. And tool responses actively push the loop: `submit_workflow`
returning cleanly says "valid, not correct — now LOOK"; a rejection says "not an
iteration — fix the named node and re-submit."

## Watch the loop actually work

Driven entirely through this MCP server against a real ComfyUI (RTX 4090, SD1.5),
brief: *"a crisp, sharply focused macro studio photo of a single red apple on a
warm wooden table, fine skin texture, rich detail."* Seed fixed at 42 so each
pass changes exactly **one** knob and the effect is attributable. The objective
metric is variance-of-Laplacian (a standard sharpness/focus measure).

![Five loop passes, left to right: a soft flat apple sharpens into a crisp, saturated, richly textured one](loop_demo.png)

| Pass | One change | Sharpness (varLap) | Verdict by **looking** |
|---|---|---:|---|
| 1 | baseline — 6 steps, cfg 2.5 | 425 | Soft, flat, matte. Weakest. |
| 2 | steps 6 → 24 | **1204** | Sharper — but the high number is the **wood grain**, apple skin still plasticky. |
| 3 | cfg 2.5 → 7.5 | 515 | Apple gets *richer* (saturated, skin speckles) — metric **drops** because the background softened. |
| 4 | euler → dpmpp_2m + karras | 740 | **Winner.** Crisp highlight, visible lenticels, believable wood. |
| 5 | steps 24 → 36 | 661 | ≈ pass 4. Diminishing returns → **stop.** |

The lesson the loop is built on, caught live: **the metric peaked at pass 2, but
pass 2 is not the best image** — its score was inflated by background texture,
not apple detail. The winner (pass 4) was chosen by *looking*. A green number is
*valid, not correct*. ([example_apple.png](example_apple.png) is that pass-4 result.)

## Install

```bash
cd mcp
pip install -e .            # or: uv pip install -e .
```

## Connect (Claude Code)

```bash
claude mcp add comfyui -- comfy-mcp
```

Or wire it manually in your MCP client config:

```json
{
  "mcpServers": {
    "comfyui": {
      "command": "comfy-mcp",
      "env": { "COMFYUI_URL": "http://localhost:8188" }
    }
  }
}
```

## Config

| Env var | Default | Purpose |
|---|---|---|
| `COMFYUI_URL` | `http://localhost:8188` | Your ComfyUI server |
| `COMFYUI_ONBOARDING_DIR` | repo root above this package | Where the `comfy_loop` / `comfy_skill` prompts read their markdown |

## Use it

1. In your agent, load the **`comfy_loop`** prompt (or let it read the
   `comfyui://loop-method` resource) to pull in the method.
2. Give it a goal. It will `check_comfyui` → `list_nodes` / `get_node` /
   `list_models` → build API-format JSON → `submit_workflow` →
   `get_result` → `get_image`, then critique and iterate.

## License

MIT.
