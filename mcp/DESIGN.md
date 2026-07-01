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
  version." We have no pinning, no rollback, no snapshot, no conflict resolution.
  A curated catalog exists precisely to eliminate this failure class.
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
2. **Repair: catalog-informed and version-pinned.** Use a template's declared
   `requiresCustomNodes` / `models` as the plan, but install *known-good pinned
   versions*, not "latest" — "latest" is what turns repair into conflict roulette.
   Verify the result against live `object_info` before continuing.
3. **Honest failure is a feature.** When a graph can't run on this box, say
   *exactly* why — which node/model is missing or conflicting — and stop. A
   controlled cloud never has to do this; for a local tool it's a real
   deliverable, and our loop's `node_errors` path already does it.

## Concrete implications for the roadmap

- **Keep:** live `object_info` discovery, compact-node transport, the
  look→critique→iterate loop, honest `node_errors` surfacing.
- **Fix:** the install path — move from "install latest pack" toward
  **version-pinned, dependency-aware** installs; improve class→pack resolution
  (it currently takes the first registry match and can pick the wrong pack).
- **Add (informed by the cloud, on-identity):** enrich the template index with
  `requiresCustomNodes`/`models`/`usage` (already in the open catalog data);
  `run_template` with input overrides (run known-good graphs without loading the
  JSON); typed `list_nodes` filters. See the README/roadmap.
- **The one real gap:** model *discovery + download*. Installing missing nodes but
  not missing models is a half-repair. This is the highest-value repair addition —
  and it must follow the same rule: pinned, verified against ground truth.
- **Don't chase:** becoming a cloud catalog or a `cql`-style graph query engine.
  Those are the cloud's game; they don't serve the local/loop identity.

## One-line summary

We are better at **knowing the truth of your box**; we are worse at **fixing it
reliably**, and we currently ignore its worst failure mode. Double down on
ground-truth discovery; make repair catalog-informed and version-pinned; be
honest when a graph can't run. That is the defensible local position — not a
blanket claim of superiority.
