"""
comfy-mcp — a loop-aware MCP server for ComfyUI.

It wraps the ComfyUI REST API as MCP **tools**, exposes the build→run→look→
critique→fix methodology as MCP **prompts**, and serves the live node truth +
the onboarding docs as MCP **resources**.

The point isn't just "call the API." Every tool description and response nudges
the model through the loop: discover before building, validate by executing,
and — the step models skip — *actually look at the pixels* before deciding a
graph is done. A graph with zero node_errors is valid, not correct.

Config via env:
  COMFYUI_URL             base URL of the ComfyUI server (default http://localhost:8188)
  COMFYUI_ONBOARDING_DIR  dir holding the loop/skill markdown (default: repo root above this pkg)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP, Image

from .compress import (
    _NODE_LEGEND,
    compact_node,
    flowzip_deflate,
    flowzip_inflate,
    litegraph_to_api,
)

COMFY_URL = os.environ.get("COMFYUI_URL", "http://localhost:8188").rstrip("/")

# The official, open template catalog — the same repo the Cloud MCP's template
# search is built from. Lets us browse/fetch all ~500 templates WITHOUT installing
# them, straight from GitHub. Override the ref with COMFYUI_TEMPLATES_REF.
_TPL_REPO = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates"
_TPL_REF = os.environ.get("COMFYUI_TEMPLATES_REF", "main")
_TPL_BASE = f"{_TPL_REPO}/{_TPL_REF}"

# The loop/skill docs are single-sourced from the repo so the MCP prompts never
# drift from the pasteable prompts. Override the dir if the server is installed
# away from the repo.
_DOCS_DIR = Path(
    os.environ.get("COMFYUI_ONBOARDING_DIR", str(Path(__file__).resolve().parents[2]))
)

mcp = FastMCP(
    "comfyui",
    instructions=(
        "Tools + prompts for building ComfyUI workflows the reliable way.\n\n"
        "ALWAYS: discover real nodes/models from the live API (list_nodes / "
        "get_node / list_models) before writing JSON — never guess a node name, "
        "input name, type, or model filename. Build API/prompt format. Validate "
        "by executing (submit_workflow); node_errors are not iterations — read "
        "them, fix that node, re-submit.\n\n"
        "PREFER LOOPING when the goal is a good *result*, not just a graph that "
        "runs — i.e. whenever a trained eye could reject the output: composition/"
        "count, likeness, matte/edge quality, upscale/restore, relight, texture "
        "seams, video temporal stability, 'make it look right'. Then run the loop: "
        "get_result -> get_image to actually LOOK, name ONE concrete defect, change "
        "ONE parameter, re-run — repeat until you cannot name a real defect, then "
        "present the result + a per-pass log and ask for sign-off. A graph with "
        "zero node_errors is VALID, NOT CORRECT — never trust a green run or a "
        "single metric; judge by looking. Load the `comfy_loop` prompt for the "
        "full autonomous method.\n\n"
        "SKIP the loop only for mechanical, non-aesthetic tasks (a format "
        "conversion, a one-shot where the user explicitly wants just a runnable "
        "graph, or a pure API query). When unsure, do at least one look-and-"
        "critique pass before declaring done."
    ),
)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=COMFY_URL, timeout=30.0)


def _read_doc(name: str) -> str:
    path = _DOCS_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return f"(could not read {name} from {_DOCS_DIR} — set COMFYUI_ONBOARDING_DIR)"


def _extract_enum(spec: Any) -> list[str] | None:
    """A loader input's valid files live in one of two encodings.

    Legacy: type is a list → element 0 is the allowed values.
    Newer:  type is "COMBO" → element 1 is {"options": [...]}.
    """
    if not isinstance(spec, list) or not spec:
        return None
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        opts = spec[1].get("options")
        return list(opts) if isinstance(opts, list) else None
    if isinstance(spec[0], list):
        return [str(x) for x in spec[0]]
    return None


# --------------------------------------------------------------------------- #
# DISCOVER
# --------------------------------------------------------------------------- #
@mcp.tool()
async def check_comfyui() -> str:
    """Confirm ComfyUI is up and reachable (loop step 0).

    Returns the installed-node count and device/VRAM. If this fails, ComfyUI
    isn't running or is on another port — do not start guessing node names.
    """
    try:
        async with _client() as c:
            info = (await c.get("/object_info")).json()
            stats = (await c.get("/system_stats")).json()
    except Exception as e:  # noqa: BLE001
        return (
            f"ComfyUI is NOT reachable at {COMFY_URL} ({e}). "
            "Check the host/port, or start ComfyUI. Do not generate workflow "
            "JSON until the API answers — you'd only be guessing node names."
        )
    devices = ", ".join(
        f"{d.get('name')} {round(d.get('vram_total', 0) / 1e9, 1)}GB"
        for d in stats.get("devices", [])
    )
    return f"ComfyUI up at {COMFY_URL}: {len(info)} nodes installed. Devices: {devices or 'n/a'}."


@mcp.tool()
async def list_nodes(keyword: str = "") -> str:
    """Search installed nodes by keyword (loop step: discover, don't guess).

    Matches the keyword against BOTH the class name and the display name, so a
    node found by its UI label ("Load Image") still turns up. Returns
    class_name -> display_name. Class names are case-sensitive and differ from
    display names — use the class_name in workflow JSON. Omit keyword to get the
    total count only (the full list is large).
    """
    async with _client() as c:
        info: dict[str, Any] = (await c.get("/object_info")).json()
    kw = keyword.lower().strip()
    if not kw:
        return f"{len(info)} nodes installed. Pass a keyword to filter, or use get_node for one node's exact interface."
    hits = {
        k: info[k].get("display_name", k)
        for k in sorted(info)
        if kw in k.lower() or kw in str(info[k].get("display_name", "")).lower()
    }
    if not hits:
        return f"No node matches '{keyword}' by class or display name. Try a broader keyword."
    lines = [
        f"{k}  ->  {v}" + ("   (matched display name)" if kw not in k.lower() else "")
        for k, v in hits.items()
    ]
    return f"{len(hits)} match '{keyword}' (class or display name):\n" + "\n".join(lines)


@mcp.tool()
async def get_node(class_name: str, verbose: bool = False) -> str:
    """Get one node's interface: inputs (required +, optional ?) and outputs (-).

    Default is COMPACT notation — `@Name +req:T ?opt:T -out:T` (type codes:
    {legend}) — ~90% fewer tokens than raw JSON, enough to wire the node
    correctly. Pass verbose=True for the full JSON (defaults, min/max, tooltips)
    when you need exact widget ranges.
    """.replace("{legend}", _NODE_LEGEND)
    async with _client() as c:
        r = await c.get(f"/object_info/{class_name}")
    if r.status_code != 200 or not r.json():
        return f"No node class '{class_name}'. Use list_nodes to find the correct case-sensitive class name."
    spec = r.json().get(class_name, r.json())
    if verbose:
        return json.dumps(spec, indent=2)
    return f"# {_NODE_LEGEND}\n{compact_node(class_name, spec)}"


@mcp.tool()
async def list_models(class_name: str, input_name: str = "") -> str:
    """List the real model files a loader offers (loop step: never hallucinate a
    checkpoint/LoRA/VAE filename).

    Reads the loader's enum from object_info (handles both the legacy list and
    the newer COMBO encoding). If input_name is omitted, reports every enum-typed
    input on the node. Pick ONLY from the returned list.
    """
    async with _client() as c:
        r = await c.get(f"/object_info/{class_name}")
    if r.status_code != 200 or not r.json():
        return f"No node class '{class_name}'. Use list_nodes first."
    spec = r.json()[class_name]["input"]
    fields = {**spec.get("required", {}), **spec.get("optional", {})}
    if input_name:
        enum = _extract_enum(fields.get(input_name))
        if enum is None:
            return f"'{input_name}' on {class_name} is not an enum/model input."
        return f"{class_name}.{input_name} ({len(enum)} files):\n" + "\n".join(enum)
    out = []
    for name, field_spec in fields.items():
        enum = _extract_enum(field_spec)
        if enum is not None:
            preview = enum[:50]
            more = "" if len(enum) <= 50 else f" (+{len(enum) - 50} more)"
            out.append(f"{name} ({len(enum)}):\n  " + "\n  ".join(preview) + more)
    return f"Enum inputs on {class_name}:\n\n" + "\n\n".join(out) if out else f"{class_name} has no enum inputs."


async def _online_index() -> list[dict]:
    """Flatten the official template catalog's index.json into {name,title,description}."""
    async with httpx.AsyncClient(timeout=20.0) as c:
        cats = (await c.get(f"{_TPL_BASE}/templates/index.json")).json()
    out: list[dict] = []
    for cat in cats:
        title = cat.get("title", cat.get("moduleName", ""))
        for t in cat.get("templates", []):
            out.append(
                {
                    "name": t.get("name", ""),
                    "title": t.get("title", ""),
                    "description": t.get("description", ""),
                    "category": title,
                }
            )
    return out


@mcp.tool()
async def search_templates(keyword: str = "", source: str = "online") -> str:
    """Search known-good workflow templates to adapt (few-shot beats zero-shot).

    source="online" (default): the OFFICIAL open catalog on GitHub
    (Comfy-Org/workflow_templates) — the same ~500-template set the Cloud MCP's
    search is built from. You do NOT need these installed; they're browsed
    straight from the repo. Matches keyword against name + title + description.

    source="installed": only templates on THIS ComfyUI right now
    (/api/workflow_templates — every installed pack's example workflows). Smaller,
    but guaranteed runnable on your install without adding anything.

    Then fetch one with get_template. Note an online template may reference nodes/
    models you haven't installed — reconcile against object_info before running.
    """
    kw = keyword.lower().strip()
    if source == "installed":
        async with _client() as c:
            idx: dict[str, list[str]] = (await c.get("/api/workflow_templates")).json()
        total = sum(len(v) for v in idx.values())
        lines: list[str] = []
        for pack in sorted(idx):
            hits = [n for n in idx[pack] if not kw or kw in n.lower() or kw in pack.lower()]
            if hits:
                lines.append(f"{pack}:")
                lines.extend(f"  {n}" for n in hits)
        if not lines:
            return f"No installed template matches '{keyword}' among {total} in {len(idx)} packs. Try source='online' for the full catalog."
        return (
            f"{total} installed templates in {len(idx)} packs"
            + (f" — matching '{keyword}'" if kw else "")
            + ". Fetch with get_template(pack, name, source='installed'):\n"
            + "\n".join(lines)
        )

    # online catalog
    try:
        entries = await _online_index()
    except Exception as e:  # noqa: BLE001
        return f"Could not reach the online template catalog ({e}). Try source='installed'."
    hits = [
        e for e in entries
        if not kw or kw in e["name"].lower() or kw in e["title"].lower() or kw in e["description"].lower()
    ]
    if not hits:
        return f"No template in the online catalog ({len(entries)} total) matches '{keyword}'. Try a broader keyword."
    lines = [f"  {e['name']}  —  {e['title']}  [{e['category']}]" for e in hits[:60]]
    more = "" if len(hits) <= 60 else f"\n  … (+{len(hits) - 60} more; narrow the keyword)"
    return (
        f"{len(hits)} of {len(entries)} online catalog templates"
        + (f" matching '{keyword}'" if kw else "")
        + ". Fetch with get_template(name=<name>, source='online'):\n"
        + "\n".join(lines) + more
    )


@mcp.tool()
async def get_template(name: str, pack: str = "", source: str = "online", fmt: str = "flowzip") -> str:
    """Fetch one workflow template as a known-good starting point.

    source="online" (default): from the official GitHub catalog — no install
    needed; `pack` is ignored. source="installed": from this ComfyUI (`pack`
    required).

    fmt="flowzip" (default): compact FlowZip text — ~85% fewer tokens than the raw
    litegraph JSON, enough to read/adapt the graph. fmt="json": the full litegraph.
    Either way it's litegraph, NOT the API/prompt format submit_workflow needs —
    adapt to API (resolve passthroughs, widgets_values -> named inputs via
    get_node), or inflate a FlowZip with inflate_workflow. If from the online
    catalog, first confirm you have its nodes/models — run find_missing_nodes then
    install_node_pack, or verify with list_nodes/list_models.
    """
    if source == "installed":
        if not pack:
            return "source='installed' needs a pack. Use search_templates(source='installed') for valid pack/name pairs."
        async with _client() as c:
            r = await c.get(f"/api/workflow_templates/{pack}/{name}.json")
        label = f"{pack}/{name} (installed)"
    else:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{_TPL_BASE}/templates/{name}.json")
        label = f"{name} (online catalog)"
    if r.status_code != 200:
        return f"No template '{name}' (HTTP {r.status_code}). Use search_templates to list valid names."
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return f"Template '{label}' did not return JSON."
    is_ui = isinstance(data, dict) and "nodes" in data and "links" in data
    hint = "" if source == "installed" else " (confirm nodes/models via find_missing_nodes)"
    if fmt == "flowzip" and is_ui:
        return (
            f"Template {label} — FlowZip (litegraph; inflate_workflow to expand, "
            f"then adapt to API to run){hint}\n\n{flowzip_deflate(data)}"
        )
    kind = "litegraph — adapt to API before submitting" if is_ui else "inspect before submitting"
    return f"Template {label} — {kind}{hint}\n\n" + json.dumps(data, indent=2)


@mcp.tool()
async def inflate_workflow(flowzip: str) -> str:
    """Expand FlowZip text back into a full litegraph workflow JSON.

    Reverse of the FlowZip that get_template returns. Structure (nodes, types,
    links, widget values) is preserved; cosmetic fields are not. To RUN it, still
    adapt the litegraph to API/prompt format for submit_workflow.
    """
    try:
        wf = flowzip_inflate(flowzip)
    except Exception as e:  # noqa: BLE001
        return f"Could not parse FlowZip: {e}"
    return json.dumps(wf, indent=2)


@mcp.tool()
async def flowzip_to_api(flowzip: str) -> str:
    """Convert FlowZip (or litegraph) into API/prompt format ready for submit_workflow.

    Inflates FlowZip if needed, then maps the litegraph to the flat API graph using
    the live object_info (resolves links, maps widget values to named inputs). This
    is the bridge for authoring/adapting graphs compactly and running them.

    Best-effort: subgraph instances and unknown nodes are skipped and reported;
    widget drift between an old template and a newer node shows up as a
    node_errors when you submit_workflow the result — read it, fix that node,
    re-submit. Review the API graph before running.
    """
    text = flowzip.strip()
    try:
        wf = flowzip_inflate(text) if not text.startswith("{") else json.loads(text)
    except Exception as e:  # noqa: BLE001
        return f"Could not parse input: {e}"
    async with _client() as c:
        oi = (await c.get("/object_info")).json()
    api, warnings = litegraph_to_api(wf, oi)
    note = ("\n\nSkipped (handle manually): " + "; ".join(warnings)) if warnings else ""
    return (
        f"API/prompt format ({len(api)} nodes). Review, then submit_workflow. "
        "A green run is valid, not correct — LOOK at the output." + note
        + "\n\n" + json.dumps(api, indent=2)
    )


# --------------------------------------------------------------------------- #
# EXTEND — install what a template needs (via ComfyUI-Manager, trusted registry)
# --------------------------------------------------------------------------- #
def _node_classes(workflow: Any) -> set[str]:
    """Extract real node class names from either format.

    API format: values keyed by node id, each with class_type.
    Litegraph: top-level `nodes[].type`, PLUS nodes nested inside
    `definitions.subgraphs[].nodes`. A subgraph *instance* has type == the
    subgraph's id (a UUID) — those aren't installable classes, so exclude them
    and descend into the definition instead.
    """
    classes: set[str] = set()
    subgraph_ids: set[str] = set()
    if isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list):
        for n in workflow["nodes"]:
            if n.get("type"):
                classes.add(n["type"])
        for sg in workflow.get("definitions", {}).get("subgraphs", []):
            if sg.get("id"):
                subgraph_ids.add(sg["id"])
            for n in sg.get("nodes", []):
                if n.get("type"):
                    classes.add(n["type"])
    elif isinstance(workflow, dict):
        for n in workflow.values():
            if isinstance(n, dict) and n.get("class_type"):
                classes.add(n["class_type"])
    virtual = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "Reroute (rgthree)"}
    return {c for c in classes if c not in virtual and c not in subgraph_ids}


@mcp.tool()
async def find_missing_nodes(name: str = "", pack: str = "", source: str = "online") -> str:
    """Diff a template's nodes against what's installed, and resolve each missing
    node to the pack that provides it (via ComfyUI-Manager's registry mapping).

    Fetches the template (same args as get_template), lists the node classes it
    uses, subtracts what /object_info already has, and for each missing class
    reports the installable pack id to pass to install_node_pack. Read-only.
    """
    # fetch template
    if source == "installed":
        if not pack:
            return "source='installed' needs a pack."
        url = f"/api/workflow_templates/{pack}/{name}.json"
        async with _client() as c:
            data = (await c.get(url)).json()
    else:
        async with httpx.AsyncClient(timeout=20.0) as c:
            data = (await c.get(f"{_TPL_BASE}/templates/{name}.json")).json()
    needed = _node_classes(data)
    if not needed:
        return f"Template '{name}' has no resolvable node classes."

    async with _client() as c:
        installed = set((await c.get("/object_info")).json())
        mappings = (await c.get("/customnode/getmappings?mode=cache")).json()
        catalog = (await c.get("/customnode/getlist?mode=cache")).json()
    packs = catalog.get("node_packs", catalog) if isinstance(catalog, dict) else {}

    missing = sorted(needed - installed)
    if not missing:
        return f"All {len(needed)} node classes in '{name}' are already installed. Ready to build/run."

    # class -> source key (url or id) via getmappings
    def resolve(cls: str) -> tuple[str, str] | None:
        for src, val in mappings.items():
            names = val[0] if isinstance(val, list) and val else []
            if cls in names:
                title = val[1].get("title_aux", src) if len(val) > 1 and isinstance(val[1], dict) else src
                return src, title
        return None

    # source key -> installable CNR id via getlist (match id / reference / repository / files)
    def to_pack_id(src: str) -> str | None:
        if isinstance(packs, dict) and src in packs:
            return src
        if isinstance(packs, dict):
            for pid, info in packs.items():
                refs = {info.get("reference"), info.get("repository")} | set(info.get("files", []) or [])
                if src in refs or src.rstrip("/") in {str(r).rstrip("/") for r in refs if r}:
                    return pid
        return None

    lines, unresolved = [], []
    for cls in missing:
        r = resolve(cls)
        if not r:
            unresolved.append(cls)
            continue
        src, title = r
        pid = to_pack_id(src)
        if pid:
            lines.append(f"  {cls}  ->  pack id '{pid}'  ({title})")
        else:
            lines.append(f"  {cls}  ->  {title}  [{src}] (not in CNR registry; install by git url)")
    out = [f"{len(missing)} missing node class(es) in '{name}':", *lines]
    if unresolved:
        out.append("\nCould not resolve to a pack (search ComfyUI-Manager manually): " + ", ".join(unresolved))
    out.append("\nInstall with install_node_pack(pack_id), then restart_comfyui, then re-check.")
    return "\n".join(out)


@mcp.tool()
async def install_node_pack(pack_id: str, version: str = "latest") -> str:
    """Install a custom-node pack by its ComfyUI-Manager registry id (from
    find_missing_nodes) — trusted registry only, no arbitrary code.

    Queues the install, starts the queue, and polls until done. A ComfyUI RESTART
    is required afterward before /object_info reflects the new nodes — call
    restart_comfyui, then re-query. Fails clearly if Manager's security level
    blocks API installs.
    """
    import anyio

    async with _client() as c:
        payload = {"id": pack_id, "version": version, "selected_version": version, "skip_post_install": False}
        r = await c.post("/manager/queue/install", json=payload)
        if r.status_code == 403:
            return ("Blocked by ComfyUI-Manager security level. Lower it (Manager settings) "
                    "or install this pack manually on the ComfyUI host, then restart.")
        if r.status_code != 200:
            return f"Install request failed (HTTP {r.status_code}): {r.text[:300]}"
        await c.post("/manager/queue/start")
        status = {}
        with anyio.move_on_after(180):
            while True:
                status = (await c.get("/manager/queue/status")).json()
                if status.get("is_processing") is False and status.get("done_count", 0) >= status.get("total_count", 0):
                    break
                await anyio.sleep(2.0)
    return (
        f"Queued + processed install of '{pack_id}' (status: {json.dumps(status)[:200]}).\n"
        "RESTART REQUIRED: call restart_comfyui, then re-run find_missing_nodes / get_node to confirm "
        "the new nodes registered before building."
    )


@mcp.tool()
async def restart_comfyui() -> str:
    """Restart ComfyUI (via ComfyUI-Manager) so newly installed nodes register in
    /object_info. The server is briefly unavailable; poll check_comfyui after.
    """
    try:
        async with _client() as c:
            await c.post("/manager/reboot")
    except Exception:  # noqa: BLE001
        pass  # the reboot drops the connection — expected
    return ("Restart triggered. ComfyUI is coming back up — wait a few seconds, then call "
            "check_comfyui to confirm it's live and the new nodes are registered.")


# --------------------------------------------------------------------------- #
# RUN
# --------------------------------------------------------------------------- #
@mcp.tool()
async def submit_workflow(workflow: dict, client_id: str = "comfy-mcp") -> str:
    """Queue an API-format workflow for execution (loop step 2: RUN).

    `workflow` is the flat API/prompt-format dict: {node_id: {class_type, inputs}}.
    Do NOT pass litegraph/UI format here.

    On success: returns the prompt_id — then call get_result to fetch outputs and
    get_image to LOOK at them. Running with zero errors means the graph is VALID,
    not CORRECT — you still have to inspect the pixels.
    On failure: returns node_errors keyed by node id. That is NOT an iteration —
    read the error, fix that specific node, and re-submit until it executes.
    """
    async with _client() as c:
        r = await c.post("/prompt", json={"prompt": workflow, "client_id": client_id})
    if r.status_code == 200:
        pid = r.json().get("prompt_id")
        return (
            f"Queued. prompt_id={pid}\n"
            "Now: get_result(prompt_id) for output filenames, then get_image to "
            "actually LOOK. Zero node_errors = valid, not correct — inspect the "
            "output against the brief and name any concrete defect before deciding."
        )
    try:
        err = r.json()
    except Exception:  # noqa: BLE001
        err = r.text
    return (
        f"REJECTED (HTTP {r.status_code}). This is not an iteration — fix the "
        f"named node(s) and re-submit:\n{json.dumps(err, indent=2) if isinstance(err, dict) else err}"
    )


@mcp.tool()
async def get_result(prompt_id: str, timeout_s: float = 120.0) -> str:
    """Poll /history for a submitted prompt and return its output files (loop
    step 3: LOOK — part 1, find what was produced).

    Blocks up to timeout_s for the run to finish. Returns each output's
    filename / subfolder / type — feed those to get_image to view the pixels.
    """
    import anyio

    deadline_hit = True
    async with _client() as c:
        with anyio.move_on_after(timeout_s):
            while True:
                hist = (await c.get(f"/history/{prompt_id}")).json()
                if prompt_id in hist:
                    deadline_hit = False
                    break
                await anyio.sleep(1.0)
    if deadline_hit:
        return f"Still running after {timeout_s}s. Call get_result again, or check get_queue."

    outputs = hist[prompt_id].get("outputs", {})
    files: list[dict[str, str]] = []
    for node_out in outputs.values():
        for key in ("images", "gifs", "videos"):
            for item in node_out.get(key, []):
                files.append(
                    {
                        "filename": item.get("filename", ""),
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    }
                )
    if not files:
        return f"Run finished but produced no image/video outputs. Check the graph has a Save/Preview node. Raw outputs: {json.dumps(outputs)[:500]}"
    return (
        f"{len(files)} output(s) for {prompt_id}:\n"
        + json.dumps(files, indent=2)
        + "\n\nNEXT (do not stop here): call get_image on each and LOOK. Then either "
        "(a) name ONE concrete defect vs the brief — six fingers, drifted background, "
        "hard matte edge, over-strong effect, wrong count — change exactly ONE "
        "parameter and re-submit; or (b) if you genuinely cannot name a defect, "
        "declare the brief met and present the result for sign-off. A green run is "
        "valid, not correct — decide by looking, never by a single metric."
    )


@mcp.tool()
async def get_image(filename: str, subfolder: str = "", image_type: str = "output") -> Image:
    """Fetch a rendered output so you can LOOK at it (loop step 3: LOOK — part 2).

    Returns the actual image to the model. This is the step that makes the loop
    work: don't declare a workflow done off a green run — view the pixels, judge
    them against the brief, then change one thing and re-run.
    """
    async with _client() as c:
        r = await c.get(
            "/view",
            params={"filename": filename, "subfolder": subfolder, "type": image_type},
        )
    r.raise_for_status()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    fmt = {"jpg": "jpeg", "jpeg": "jpeg", "webp": "webp"}.get(ext, "png")
    return Image(data=r.content, format=fmt)


@mcp.tool()
async def upload_image(path: str, overwrite: bool = True) -> str:
    """Upload a local image into ComfyUI's input/ dir so a LoadImage node can use it.

    Returns the name to reference in the workflow. (Video nodes like VHS_LoadVideo*
    read files you place directly in ComfyUI's input/ dir instead.)
    """
    p = Path(path)
    if not p.is_file():
        return f"No file at {path}."
    async with _client() as c:
        files = {"image": (p.name, p.read_bytes())}
        data = {"overwrite": "true" if overwrite else "false"}
        r = await c.post("/upload/image", files=files, data=data)
    if r.status_code != 200:
        return f"Upload failed (HTTP {r.status_code}): {r.text}"
    name = r.json().get("name", p.name)
    return f"Uploaded as '{name}'. Reference it in a LoadImage node's image input."


# --------------------------------------------------------------------------- #
# CONTROL
# --------------------------------------------------------------------------- #
@mcp.tool()
async def system_stats() -> str:
    """Report device / VRAM usage — useful when tuning resolution/batch or after an OOM."""
    async with _client() as c:
        stats = (await c.get("/system_stats")).json()
    return json.dumps(stats, indent=2)


@mcp.tool()
async def get_queue() -> str:
    """Show what's running and pending in ComfyUI's execution queue."""
    async with _client() as c:
        q = (await c.get("/queue")).json()
    running = len(q.get("queue_running", []))
    pending = len(q.get("queue_pending", []))
    return f"Queue: {running} running, {pending} pending.\n{json.dumps(q, indent=2)[:1500]}"


@mcp.tool()
async def interrupt() -> str:
    """Cancel the currently executing prompt."""
    async with _client() as c:
        await c.post("/interrupt")
    return "Interrupt sent."


# --------------------------------------------------------------------------- #
# PROMPTS — the loop methodology, so any client can pull it in
# --------------------------------------------------------------------------- #
@mcp.prompt(title="ComfyUI build-and-loop method")
def comfy_loop() -> str:
    """The full autonomous build→run→look→critique→fix loop prompt.

    Load this at the start of a ComfyUI task to give the model the whole method:
    discover from the live API, build API format, validate by executing, then
    iterate on the rendered output until it meets the brief.
    """
    return _read_doc("COMFYUI_WORKFLOW_LOOP_PROMPT.md")


@mcp.prompt(title="ComfyUI workflow skill")
def comfy_skill() -> str:
    """The compact skill version of the method (discover → build → validate → iterate)."""
    return _read_doc("SKILL.md")


# --------------------------------------------------------------------------- #
# RESOURCES — live truth + docs
# --------------------------------------------------------------------------- #
@mcp.resource("comfyui://object_info")
async def object_info_resource() -> str:
    """The live, full /object_info dump — every installed node's exact interface."""
    async with _client() as c:
        return (await c.get("/object_info")).text


@mcp.resource("comfyui://loop-method")
def loop_method_resource() -> str:
    """The build-and-loop prompt as a readable resource."""
    return _read_doc("COMFYUI_WORKFLOW_LOOP_PROMPT.md")


@mcp.resource("comfyui://skill")
def skill_resource() -> str:
    """The ComfyUI workflow skill as a readable resource."""
    return _read_doc("SKILL.md")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
