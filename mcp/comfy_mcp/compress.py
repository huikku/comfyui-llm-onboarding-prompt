"""Token-efficient representations for ComfyUI nodes and workflows.

Clean-room implementations of two compact text formats, written to the
documented specs (not copied from any source):

1. Compact node notation — `@Name +req:T ?opt:T -out:T` — for discovery output.
   Turns verbose /object_info (~1 MB) into a form ~98% smaller so a looping agent
   doesn't re-pay huge token cost every iteration.

2. FlowZip — a compact, reversible serialization of a litegraph workflow
   (nodes + links) for handing graphs to / from the model cheaply.

The two use SEPARATE type-code tables on purpose (they serialize different
things); don't cross them.
"""

from __future__ import annotations

import json
from typing import Any

# --------------------------------------------------------------------------- #
# 1) Compact node notation
# --------------------------------------------------------------------------- #
# Codes for the compact discovery format. LATENT=A, LIST=L here.
_NODE_CODES = {
    "MODEL": "M", "IMAGE": "G", "CONDITIONING": "C", "LATENT": "A", "VAE": "V",
    "CLIP": "P", "STRING": "S", "INT": "I", "FLOAT": "F", "BOOLEAN": "B",
    "MASK": "K", "CONTROL_NET": "T",
}
_NODE_LEGEND = "M=MODEL G=IMAGE C=CONDITIONING A=LATENT V=VAE P=CLIP S=STRING I=INT F=FLOAT B=BOOLEAN K=MASK T=CONTROL_NET L=enum/list *=other"


def _node_type_code(t: Any) -> str:
    """Map an object_info input/output type to a compact code.

    A list (or COMBO) type is an enum of choices -> 'L'. Known types get a
    letter; anything else passes through as its own name.
    """
    if isinstance(t, list) or t == "COMBO":
        return "L"
    if isinstance(t, str):
        return _NODE_CODES.get(t, t)
    return "*"


def compact_node(name: str, spec: dict) -> str:
    """Render one /object_info entry as `@Name +req:T ?opt:T -out:T`."""
    parts = [f"@{name}"]
    inp = spec.get("input", {}) if isinstance(spec, dict) else {}
    for iname, ispec in (inp.get("required") or {}).items():
        t = ispec[0] if isinstance(ispec, list) and ispec else ispec
        parts.append(f"+{iname}:{_node_type_code(t)}")
    for iname, ispec in (inp.get("optional") or {}).items():
        t = ispec[0] if isinstance(ispec, list) and ispec else ispec
        parts.append(f"?{iname}:{_node_type_code(t)}")
    outs = spec.get("output", []) or []
    onames = spec.get("output_name") or outs
    for i, ot in enumerate(outs):
        oname = onames[i] if i < len(onames) and onames[i] else (ot if isinstance(ot, str) else "out")
        parts.append(f"-{oname}:{_node_type_code(ot)}")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# 2) FlowZip — reversible litegraph (de)serialization
# --------------------------------------------------------------------------- #
# FlowZip link type codes (LATENT=L here — different table from above).
_FZ_CODE = {
    "MODEL": "M", "IMAGE": "G", "CONDITIONING": "C", "LATENT": "L", "VAE": "V",
    "CLIP": "P", "STRING": "S", "INT": "I", "FLOAT": "F", "BOOLEAN": "B",
    "MASK": "K", "CONTROL_NET": "T", "CLIP_VISION_OUTPUT": "CO",
    "CLIP_VISION": "CV", "VOXEL": "VX", "MESH": "MS",
}
_FZ_DECODE = {v: k for k, v in _FZ_CODE.items()}


def _fz_esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace(",", "\\,").replace(";", "\\;")


def _fz_split(s: str, sep: str) -> list[str]:
    """Split on `sep` respecting backslash escapes."""
    out, buf, esc = [], [], False
    for ch in s:
        if esc:
            buf.append(ch); esc = False
        elif ch == "\\":
            buf.append(ch); esc = True
        elif ch == sep:
            out.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def _fz_unesc(s: str) -> str:
    out, esc = [], False
    for ch in s:
        if esc:
            out.append(ch); esc = False
        elif ch == "\\":
            esc = True
        else:
            out.append(ch)
    return "".join(out)


def flowzip_deflate(wf: dict) -> str:
    """litegraph workflow dict -> FlowZip text. Lossy for cosmetic fields
    (colors, sizes, properties); preserves graph structure: nodes, types,
    positions, inputs/outputs, widget values, and links."""
    lines = [
        f"W:{wf.get('id','')}|r:{wf.get('revision',0)}|ln:{wf.get('last_node_id',0)}|ll:{wf.get('last_link_id',0)}",
        "NODES:",
    ]
    for n in wf.get("nodes", []):
        pos = n.get("pos", [0, 0])
        x, y = int(pos[0]), int(pos[1]) if len(pos) > 1 else 0
        ins = []
        for inp in n.get("inputs", []) or []:
            code = _FZ_CODE.get(inp.get("type", ""), inp.get("type", "*") or "*")
            link = inp.get("link")
            ins.append(f"{_fz_esc(inp.get('name',''))}:{code}:{'' if link is None else link}")
        outs = []
        for out in n.get("outputs", []) or []:
            code = _FZ_CODE.get(out.get("type", ""), out.get("type", "*") or "*")
            links = ".".join(str(x) for x in (out.get("links") or []))
            outs.append(f"{_fz_esc(out.get('name',''))}:{code}:{links}")
        widgets = ";".join(_fz_esc(w) for w in (n.get("widgets_values") or []))
        lines.append(
            f"N{n.get('id')}:{n.get('type')}|{x},{y}"
            f"|I:{','.join(ins)}|O:{';'.join(outs)}|W:{widgets}"
        )
    lines.append("LINKS:")
    for lk in wf.get("links", []) or []:
        # litegraph link: [id, from_node, from_slot, to_node, to_slot, type]
        lid, fn, fs, tn, ts, ty = (lk + [None] * 6)[:6]
        code = _FZ_CODE.get(ty, ty or "*")
        lines.append(f"L{lid}:{fn}.{fs}->{tn}.{ts}:{code}")
    return "\n".join(lines)


def flowzip_inflate(text: str) -> dict:
    """FlowZip text -> minimal litegraph workflow dict (runnable structure)."""
    wf: dict[str, Any] = {"nodes": [], "links": []}
    section = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("W:"):
            for kv in line.split("|"):
                k, _, v = kv.partition(":")
                if k == "W":
                    wf["id"] = v
                elif k == "r":
                    wf["revision"] = int(v or 0)
                elif k == "ln":
                    wf["last_node_id"] = int(v or 0)
                elif k == "ll":
                    wf["last_link_id"] = int(v or 0)
            continue
        if line == "NODES:":
            section = "nodes"; continue
        if line == "LINKS:":
            section = "links"; continue
        if section == "nodes" and line.startswith("N"):
            head, *rest = line.split("|")
            nid, _, ntype = head[1:].partition(":")
            node: dict[str, Any] = {"id": int(nid), "type": ntype}
            fields = {seg[0]: seg[2:] for seg in rest if len(seg) >= 1 and seg[1:2] == ":"}
            # position is the first bare field
            for seg in rest:
                if "," in seg and ":" not in seg.split(",")[0]:
                    try:
                        node["pos"] = [int(x) for x in seg.split(",")[:2]]
                        break
                    except ValueError:
                        pass
            if "I" in fields and fields["I"]:
                node["inputs"] = []
                for it in _fz_split(fields["I"], ","):
                    if not it:
                        continue
                    nm, code, link = (_fz_split(it, ":") + ["", "", ""])[:3]
                    node["inputs"].append({
                        "name": _fz_unesc(nm),
                        "type": _FZ_DECODE.get(code, code),
                        "link": int(link) if link else None,
                    })
            if "O" in fields and fields["O"]:
                node["outputs"] = []
                for ot in _fz_split(fields["O"], ";"):
                    if not ot:
                        continue
                    nm, code, links = (_fz_split(ot, ":") + ["", "", ""])[:3]
                    node["outputs"].append({
                        "name": _fz_unesc(nm),
                        "type": _FZ_DECODE.get(code, code),
                        "links": [int(x) for x in links.split(".") if x],
                    })
            if "W" in fields and fields["W"]:
                node["widgets_values"] = [_fz_unesc(w) for w in _fz_split(fields["W"], ";")]
            wf["nodes"].append(node)
        elif section == "links" and line.startswith("L"):
            lid, _, rest = line[1:].partition(":")
            conn, _, code = rest.rpartition(":")
            src, _, dst = conn.partition("->")
            fn, _, fs = src.partition(".")
            tn, _, ts = dst.partition(".")
            wf["links"].append([
                int(lid), int(fn), int(fs), int(tn), int(ts), _FZ_DECODE.get(code, code),
            ])
    return wf
