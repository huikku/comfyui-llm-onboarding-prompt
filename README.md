# ComfyUI LLM Onboarding Prompt

**Stop your LLM from hallucinating ComfyUI node names.**

When you ask an AI assistant (ChatGPT, Claude, Gemini, etc.) to generate ComfyUI workflows, it will often make up node names that don't exist. This prompt teaches the LLM to query ComfyUI's built-in REST API to discover exactly what nodes are installed before generating any workflow.

## The Problem

LLMs frequently:
- Invent node names that don't exist (`TBGSAM3ModelLoaderAndDownloader` vs the actual `TBGLoadSAM3Model`)
- Guess input/output parameter names incorrectly
- Create type mismatches in node connections
- Reference nodes from packs you haven't installed

## The Solution

ComfyUI exposes `/object_info` — a REST API that returns every loaded node with its exact inputs, outputs, types, and defaults. This is the same API that ComfyUI's own frontend uses. By teaching your LLM to `curl` this endpoint first, it can generate workflows using only verified, real nodes.

## Two versions

| | What it does |
|---|---|
| [`ONBOARDING_PROMPT-v2.md`](ONBOARDING_PROMPT-v2.md) **(recommended)** | Everything in v1, plus: picks the correct JSON format for the job (flat **API/prompt** format for executing via `/prompt` vs **litegraph** format for the UI), verifies that referenced model files actually exist, and **validates each workflow by executing it** and debugging from real errors before declaring it done. |
| [`ONBOARDING_PROMPT-v1.md`](ONBOARDING_PROMPT-v1.md) | The original, node-discovery-focused prompt — teaches the LLM to query `/object_info` so it stops inventing node names. Smaller and simpler if all you want is correct node usage. |

## Usage

1. **Start ComfyUI** on your machine (default: `http://localhost:8188`)
2. **Copy the contents of [`ONBOARDING_PROMPT-v2.md`](ONBOARDING_PROMPT-v2.md)** into your LLM conversation
3. **Ask the LLM to generate a workflow** — it will now query the API to verify nodes (and, in v2, run the workflow to validate it) before handing it to you

## Quick Test

Verify it works by running this while ComfyUI is running:

```bash
# List all installed nodes
curl -s http://localhost:8188/object_info | python3 -c "
import json, sys
nodes = json.load(sys.stdin)
print(f'{len(nodes)} nodes installed')
for name in sorted(nodes)[:10]:
    print(f'  {name}')
print('  ...')
"
```

## Works With

- Any LLM with tool/terminal access (Cursor, Windsurf, Gemini Code Assist, etc.)
- Any ComfyUI installation (stock or with custom nodes)
- Any operating system

## License

MIT - use it however you want.
