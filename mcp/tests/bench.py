"""Compression + conversion metrics for comfy-mcp (read-only).

Measures, across the live node set and a broad template sample:
  A. compact-node vs raw object_info size
  B. FlowZip vs raw litegraph vs a fair stripped-JSON baseline
  C. FlowZip round-trip fidelity
  D. litegraph->API conversion coverage (incl. subgraph incidence)

Run:  COMFYUI_URL=http://localhost:8188 python tests/bench.py
Needs numpy-free stdlib only + httpx + network to the GitHub template catalog.
"""
import json
import os
import statistics as st

import httpx

from comfy_mcp.compress import compact_node, flowzip_deflate, flowzip_inflate, litegraph_to_api

BASE = os.environ.get("COMFYUI_URL", "http://localhost:8188")
GH = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main"
tok = lambda s: len(s) // 4


def minify(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)


def strip_litegraph(wf):
    nodes = []
    for n in wf.get("nodes", []):
        nn = {"id": n.get("id"), "type": n.get("type")}
        ins = [{"name": i.get("name"), "link": i.get("link")} for i in (n.get("inputs") or []) if i.get("link") is not None]
        if ins:
            nn["in"] = ins
        if n.get("widgets_values"):
            nn["w"] = n["widgets_values"]
        nodes.append(nn)
    return {"nodes": nodes, "links": [l[:6] for l in wf.get("links", [])]}


def main():
    oi = httpx.get(f"{BASE}/object_info", timeout=60).json()
    raw_tot = comp_tot = 0
    for name, spec in oi.items():
        raw_tot += len(minify(spec))
        comp_tot += len(compact_node(name, spec))
    print("### A. NODE DISCOVERY (compact vs raw object_info) ###")
    print(f"nodes: {len(oi)} | raw {raw_tot // 1024} KB (~{tok(json.dumps(oi))} tok) -> compact {comp_tot // 1024} KB "
          f"({100 - 100 * comp_tot // raw_tot}% smaller)")

    inst = httpx.get(f"{BASE}/api/workflow_templates", timeout=30).json()
    targets = [(f"{BASE}/api/workflow_templates/{p}/{n}.json", n) for p, ns in inst.items() for n in ns]
    idx = httpx.get(f"{GH}/templates/index.json", timeout=30).json()
    online = [t["name"] for c in idx for t in c.get("templates", [])]
    step = max(1, len(online) // 30)
    targets += [(f"{GH}/templates/{online[i]}.json", online[i]) for i in range(0, len(online), step)]

    rows = []
    for url, _ in targets:
        try:
            wf = httpx.get(url, timeout=30).json()
        except Exception:
            continue
        if not (isinstance(wf, dict) and "nodes" in wf):
            continue
        lg, sj = len(minify(wf)), len(minify(strip_litegraph(wf)))
        try:
            fz = len(flowzip_deflate(wf))
            back = flowzip_inflate(flowzip_deflate(wf))
            rt = ({n["id"]: n["type"] for n in wf["nodes"]} == {n["id"]: n["type"] for n in back["nodes"]}
                  and {tuple(l[:5]) for l in wf.get("links", [])} == {tuple(l[:5]) for l in back.get("links", [])})
        except Exception:
            fz, rt = lg, False
        try:
            api, warns = litegraph_to_api(wf, oi)
        except Exception:
            api, warns = {}, ["crash"]
        ntot = len([n for n in wf["nodes"] if n.get("type") not in ("Note", "MarkdownNote", "Reroute")])
        rows.append(dict(lg=lg, fz=fz, sj=sj, nodes=ntot, conv=len(api), warns=len(warns), rt=rt,
                         sg=bool(wf.get("definitions", {}).get("subgraphs"))))

    n = len(rows)
    fz_lg = st.median(r["fz"] / r["lg"] for r in rows)
    sj_lg = st.median(r["sj"] / r["lg"] for r in rows)
    fz_sj = st.median(r["fz"] / r["sj"] for r in rows)
    print(f"\n### B. TEMPLATE COMPRESSION (n={n}) ###")
    print(f"FlowZip vs litegraph: {100 - int(100 * fz_lg)}% smaller | stripped-JSON vs litegraph: {100 - int(100 * sj_lg)}% smaller")
    print(f"FlowZip vs stripped-JSON: {fz_sj:.2f}x  (the DSL buys ~{100 - int(100 * fz_sj)}% over plain JSON)")
    print(f"\n### C. FLOWZIP ROUND-TRIP ###\nclean: {sum(r['rt'] for r in rows)}/{n} ({100 * sum(r['rt'] for r in rows) // n}%)")
    full = sum(1 for r in rows if r["conv"] == r["nodes"] and r["warns"] == 0)
    withsg = sum(1 for r in rows if r["sg"])
    cov = sum(r["conv"] for r in rows)
    tot = sum(r["nodes"] for r in rows)
    print(f"\n### D. litegraph->API CONVERSION ###")
    print(f"fully converted: {full}/{n} ({100 * full // n}%) | contain subgraphs: {withsg}/{n} | node coverage: {100 * cov // tot}%")


if __name__ == "__main__":
    main()
