# Design position: discovery vs. repair

A north star for `comfy-mcp`, written after benchmarking our approach against the
official Comfy Cloud MCP on live installs. It exists to keep us honest about
where a local, bring-your-own-ComfyUI tool genuinely wins, where it doesn't, and
what to build next. Read it before adding features that drift from that focus.

## The core distinction

"Handle the user's install" is really **two** problems that do not win or lose
together:

- **Discovery** — given whatever is installed, wire a graph correctly against the
  *real* node interfaces and *real* model files.
- **Repair** — given a graph that needs nodes/models this box lacks, make the box
  able to run it (install, resolve deps, match versions, restart).

Most claims of "local is better" quietly conflate the two. We are genuinely
better at **discovery** and currently **naive at repair**. Keep them separate
when reasoning about the product.

## Where local genuinely wins — discovery

This is structural, not incidental, and it's our real edge:

- **Ground truth, not a model of it.** We read the *actual runtime's*
  `/object_info`. There is zero drift between "what we think exists" and "what
  exists" — we ask the machine that will run the graph. A catalog is a
  *description* of an environment; ours *is* the environment.
- **Custom / forked / private nodes.** A bespoke node, a locally-patched pack, or
  a node in no registry is invisible to a curated catalog by construction. Our
  `object_info` sees it immediately.
- **Version drift is caught.** Observed live: a template's stored `widgets_values`
  predated a node update (a `batch_size` input was added); reading the *installed*
  node's live interface surfaced the mismatch. A catalog can carry a stale schema;
  the runtime cannot lie about itself.
- **Local model files.** `list_models` reads the actual on-disk enum — files a
  cloud catalog can't see.

For "adapt to *this* install, including the messy and the custom," reading ground
truth beats reading a catalog. This is where we should double down.

## Where local loses — repair

Honest pushback on the parts it's tempting to overclaim:

- **We only half-solved repair, and skipped the hard half.** Detecting a gap is
  easy; *fixing* it — resolve class→pack (we have already mis-resolved a
  same-named node to the wrong pack), install the right pack, resolve pip deps,
  restart, hope nothing conflicts — is fragile. The controlled cloud avoids all of
  it.
- **Dependency/version conflict is the real pain, and we do nothing about it.**
  The nastiest part of differing libraries isn't "node missing," it's "installed
  pack X, it broke pack Y / needs a different torch / needs a different ComfyUI
  version." We have no rollback, no snapshot, no conflict resolution — and note
  that *pinning is not the fix* in a latest-first ecosystem (it can worsen
  conflicts). A curated catalog is what actually eliminates this failure class,
  which is the cloud's structural advantage.
- **Reproducibility.** A controlled environment means a template is *known to run*
  — vetted, pinned, identical for everyone. Ours is a snowflake per box: works on
  A, fails on B. Their "limitation" is a reliability feature.
- **The mess is a tax, not a virtue — for most users.** Reconciling a messy
  install only helps people who *chose* custom nodes. For everyone else, "you
  never manage nodes" is the better product answer.

## Two caveats that keep us honest

1. **It's a positioning edge, not a moat.** Nothing stops a cloud MCP from adding
   a "bring your own ComfyUI" mode that reads `object_info` too. We win because we
   are *pointed* at the local problem, not because it is technically unmatchable.
2. **Reach ≠ reliability.** We can touch installs they can't; we are *less*
   reliable at making arbitrary graphs run there. Different axes — we lead on one,
   trail on the other. Don't market reach as reliability.

## North star

The best method for the differing-install problem is neither purely ours nor
theirs — it's the combination, and we're positioned to build it:

1. **Discovery: always ground truth.** Read live `/object_info`; never trust a
   cached/bundled node schema for wiring. This is non-negotiable and it's our
   advantage. (Compact-node notation is fine as a *transport* optimization, but
   the source of truth is the live API.)
2. **Repair: catalog-informed, latest-first, robust to drift.** Use a template's
   declared `requiresCustomNodes` / `models` as the plan, and **install latest** —
   that is what the ComfyUI ecosystem does (Manager defaults to latest; users
   "Update All"), so latest matches both user expectation and the rest of their
   install. Do NOT pin a pack *backward* to match a stale template: with everything
   else on the box at latest, an old pinned pack can pull incompatible deps and
   *create* the conflicts pinning was meant to avoid. Version drift (e.g. a template
   whose stored widgets predate a node update) is a *template* problem — fix it by
   **adapting to the live interface** (`object_info` + the loop's `node_errors`),
   not by downgrading the user's node. Keep `version` available for the narrow cases
   (a known-broken latest, or reproducing someone's exact setup), but don't default
   to it or build a lockfile system around it.
3. **Honest failure is a feature.** When a graph can't run on this box, say
   *exactly* why — which node/model is missing or conflicting — and stop. A
   controlled cloud never has to do this; for a local tool it's a real
   deliverable, and our loop's `node_errors` path already does it.

## Concrete implications for the roadmap

- **Keep:** live `object_info` discovery, compact-node transport, the
  look→critique→iterate loop, honest `node_errors` surfacing.
- **Fix:** class→pack resolution (it currently takes the first registry match and
  can pick the wrong pack — e.g. resolved `SimpleMath+` to a lora pack). Keep
  installing **latest** (ecosystem-correct); the real reliability work is making
  the loop robust to version drift by remapping against the live interface, not
  pinning versions.
- **Added (informed by the cloud, on-identity):** `template_slots` + `run_template`
  with input overrides — run a known-good graph without loading the JSON into
  context (litegraph→API under the hood; subgraph templates reported, not expanded).
  Still open: enrich the template index with `requiresCustomNodes`/`models`/`usage`
  (already in the open catalog data) and typed `list_nodes` filters.
- **Model *discovery + download* — now closed** (`search_models` + `install_model`).
  Reads ComfyUI-Manager's model catalog (trusted, whitelisted source), flags what's
  already installed (ground truth), and downloads into the right folder — no restart
  (loaders re-scan). This was the half-repair gap; nodes AND models are now covered.
  A broader source (HuggingFace/Civitai search, as the cloud does) remains a possible
  extension, still subject to the verify-against-ground-truth rule.
- **Don't chase:** becoming a cloud catalog or a `cql`-style graph query engine.
  Those are the cloud's game; they don't serve the local/loop identity.

## One-line summary

We are better at **knowing the truth of your box**; we are worse at **fixing it
reliably**. Double down on ground-truth discovery; install **latest**
(ecosystem-correct) and make the loop robust to version drift by adapting to the
live interface; be honest when a graph can't run. That is the defensible local
position — not a blanket claim of superiority, and not backward version-pinning
that fights how ComfyUI users actually work.
