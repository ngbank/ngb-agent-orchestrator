# Trace Reconstruction Notes

Every dispatcher run produces a single `otel.jsonl` at
`LOGS_DIR/<workflow_id>/otel.jsonl` containing the full span trace for that
workflow. This document records the shape of that file and the queries that
let you reconstruct what happened — useful for post-hoc debugging, cost /
latency analysis, and answering "where did the time actually go?".

History:

- AOS-117: per-workflow file routing via `workflow.id` span attribute.
- AOS-118: route LiteLLM proxy `llm.call` spans into the same file +
  emit a `graph.node.*` span for every dispatched node (incl. subgraphs)
  + propagate W3C `traceparent` from dispatcher to proxy so `llm.call`
  spans share the workflow's trace tree.
- AOS-170: removed the parallel `llm_token_usage.jsonl` / `llm_failures.jsonl`
  logger; token usage is now aggregated directly from `llm.call` spans in
  `otel.jsonl` via `orchestrator.litellm_callbacks.aggregate_token_usage`,
  filtered by the `workflow.stage` span attribute.
- AOS-194: renamed the `execute` workflow-stage identifier to `generate_code`
  throughout (`goose_session` stage, `NGB_WORKFLOW_STAGE`, `workflow.stage`
  span attribute, log filenames) to match the `execute_plan` → `generate_code`
  node rename from AOS-181.

---

## File layout

```
LOGS_DIR/
└── <workflow_id>/
    ├── otel.jsonl                        # all spans for the run, NDJSON
    ├── <ticket>_<wf>_plan.log            # goose plan stage stdout
    ├── <ticket>_<wf>_generate_code.log   # goose generate_code stage stdout
    └── litellm_proxy.log                 # proxy uvicorn output
```

Default `LOGS_DIR` resolves to `$XDG_STATE_HOME/ngb-agent-orchestrator/logs`.
If `XDG_STATE_HOME` is unset, fallback is `~/.local/state/ngb-agent-orchestrator/logs`.

Each line in `otel.jsonl` is a JSON span object with these top-level keys:

| Key | Notes |
|---|---|
| `name` | e.g. `workflow.run`, `graph.node.generate_plan`, `llm.call` |
| `trace_id` / `span_id` / `parent_span_id` | Standard OTel — `parent_span_id` is `null` for the root |
| `start_time` / `end_time` | Nanoseconds since epoch |
| `duration_ms` | Convenience field added by `LocalJsonFileExporter` |
| `attributes` | Span attributes — see the table in [configuration.md](configuration.md#span-types--attributes) |
| `events` / `status` / `resource` | Standard OTel |

---

## Expected span population per workflow run

For a successful plan-stage run (e.g. `dispatcher --ticket AOS-94`):

| Span | Count | Parent | Notes |
|---|---|---|---|
| `workflow.run` | 1 | (root) | Created by `instrument_graph_stream`; carries the workflow rollup |
| `graph.node.work_planner` | 1 | `workflow.run` | Top-level subgraph host node |
| `graph.node.<inner>` × 8 | 8 | `graph.node.work_planner` | `validate_input`, `check_duplicate`, `fetch_ticket`, `create_workflow_record`, `generate_plan`, `validate_plan`, `store_plan`, `post_to_jira` |
| `graph.node.await_approval` | 1 | `workflow.run` | The interrupt node |
| `graph.checkpoint` | ~13 | `workflow.run` | One per `ObservableSqliteSaver.put` |
| `goose.run` | 1 | `workflow.run` | `goose run --recipe orchestrator/work_planner/recipes/plan.yaml` |
| `llm.call` | N (~10–60) | `workflow.run` | Emitted from the proxy subprocess, parented via traceparent; carries `workflow.stage` (`plan` / `generate_code`) for per-stage token aggregation |

All spans share a single `trace_id`. A generate_code-stage run roughly doubles
`goose.run` (one per stage) and adds a second `await_pr_approval`.

---

## Reconstruction recipes

All snippets assume:

```python
import json, pathlib, collections
spans = [
    json.loads(l)
    for l in pathlib.Path("<LOGS_DIR>/<workflow_id>/otel.jsonl")
        .read_text().splitlines()
    if l.strip()
]
by_sid = {s["span_id"]: s for s in spans}
```

### Span tally

```python
collections.Counter(s["name"] for s in spans).most_common()
```

### Render the trace tree (Gantt-ish, time-ordered)

```python
children = collections.defaultdict(list)
for s in spans:
    children[s.get("parent_span_id") or "ROOT"].append(s)
for kids in children.values():
    kids.sort(key=lambda s: s["start_time"])

def walk(sid, depth):
    for s in children.get(sid, []):
        dur = round(s.get("duration_ms", 0), 1)
        print(f"{'  '*depth}- {s['name']} [{dur}ms]")
        walk(s["span_id"], depth + 1)

walk("ROOT", 0)
```

### Per-node wall time (critical-path attribution)

```python
node_spans = [s for s in spans if s["name"].startswith("graph.node.")]
for s in sorted(node_spans, key=lambda s: -s.get("duration_ms", 0))[:10]:
    print(f"{round(s['duration_ms'],1):>10.1f} ms   {s['name']}")
```

`generate_plan` should dominate (it's the LLM-driven node). If something
else shows up first, that's where to look.

### LLM cost & latency rollup

```python
llm = [s for s in spans if s["name"] == "llm.call"]
total_in  = sum(s["attributes"].get("llm.input_tokens", 0)  for s in llm)
total_out = sum(s["attributes"].get("llm.output_tokens", 0) for s in llm)
total_lat = sum(s["attributes"].get("llm.latency_ms", 0)    for s in llm)
print(f"{len(llm)} calls  →  in={total_in}  out={total_out}  lat={total_lat:.0f}ms")
```

### Attribute an `llm.call` to the node that triggered it

`llm.call` spans are parented to `workflow.run` (not directly to the
node — see [Known limitations](#known-limitations) below) so use
time-window intersection:

```python
def enclosing_node(call):
    for s in spans:
        if not s["name"].startswith("graph.node."):
            continue
        if s["start_time"] <= call["start_time"] <= s["end_time"]:
            return s["name"]
    return None

per_node = collections.Counter(enclosing_node(c) for c in llm)
for name, n in per_node.most_common():
    print(f"  {n:3d} llm.call inside {name}")
```

Because only one node runs goose at a time, the window is unambiguous.

### Find the slowest checkpoint write

```python
ckp = [s for s in spans if s["name"] == "graph.checkpoint"]
slowest = max(ckp, key=lambda s: s.get("duration_ms", 0))
print(slowest["attributes"])  # checkpoint.step, changed_channels, writes_nodes
```

### Detect interrupts / failures

```python
root = next(s for s in spans if s["name"] == "workflow.run")
print(root["attributes"].get("workflow.exit_reason"))   # completed / interrupted / error
print(root["attributes"].get("workflow.last_node"))
failed = [s for s in spans if s.get("status", {}).get("status_code") == "ERROR"]
for s in failed:
    print(s["name"], s["attributes"].get("graph.node.error"))
```

### Subgraph hierarchy

Nodes inside the `work_planner` subgraph carry a `graph.namespace`
attribute (`work_planner:<task_id>`) and are parented to the
`graph.node.work_planner` span — so `walk("ROOT", 0)` above prints them
indented. To list just the subgraph nodes:

```python
sub = [s for s in spans if s["attributes"].get("graph.namespace")]
for s in sub:
    print(s["attributes"]["graph.namespace"], "→", s["name"])
```

---

## Cross-process linkage

The dispatcher and the LiteLLM proxy are two separate Python processes
with independent OTel pipelines. They share a trace through W3C trace
context propagation:

1. `graph.utils.goose_session()` calls
   `opentelemetry.propagate.inject(carrier)` against the currently
   active span (the `workflow.run` span at goose-launch time).
2. The resulting `traceparent` (and optional `tracestate`) is forwarded
   to the proxy subprocess as `NGB_TRACEPARENT` / `NGB_TRACESTATE`.
3. `otel/litellm_proxy_setup.py::_bootstrap_proxy_otel()` extracts the
   carrier into an OTel `Context` and stashes it via
   `otel.context.set_proxy_parent_context()`.
4. `OtelLiteLLMCallback.async_log_success_event` / `_failure_event` pass
   that context to `tracer.start_as_current_span("llm.call", context=…)`,
   so every emitted `llm.call` span lands in the workflow's trace tree.

If a workflow shows orphan `llm.call` spans (i.e. `parent_span_id is None`
on a `llm.call`), the propagation chain broke — check that `goose_session`
was entered inside an active span, that `NGB_TRACEPARENT` reached the
subprocess (`grep NGB_TRACEPARENT litellm_proxy.log` won't show it, but
you can re-run with `OTEL_EXPORTERS=console` and look for the parent
trace ID in the proxy stdout).

---

## Known limitations

1. **`llm.call` parented to `workflow.run`, not to `graph.node.generate_plan`
   directly.** The injected `traceparent` reflects the span that was
   *current* in the dispatcher when `goose_session()` opened — which is
   `workflow.run`, because `_handle_debug_event` in
   `otel/instrumentation.py` starts node spans with `tracer.start_span`
   (no current-context activation) so the spans can outlive a single
   `for event in graph.stream(...)` iteration. Use the time-window query
   above to attribute calls to the node that owned them.
2. **`llm.call.duration_ms` is 0.** The span is emitted as a post-hoc
   marker after the call completes; the real elapsed time lives in the
   `llm.latency_ms` attribute.
3. **`graph.checkpoint` is a sibling of `graph.node.*`, not a child.** It
   is emitted from the SQLite checkpointer (which has no view of the
   currently executing node), so checkpoints appear under `workflow.run`
   chronologically interleaved with node spans rather than nested
   underneath them. Match `checkpoint.writes_nodes` to a node name to
   recover the association.
4. **Spans buffered in `BatchSpanProcessor` can be lost on hard exits.**
   The dispatcher uses batched export by default for performance. The
   proxy subprocess explicitly opts into `SimpleSpanProcessor`
   (`setup_tracing(synchronous=True)`) because the dispatcher kills it
   with `SIGTERM` and uvicorn's handler doesn't reliably trigger the
   `atexit` flush.
