# Graph Execution Architecture Analysis

## Overview

The orchestrator uses **LangGraph's StateGraph** as its execution engine, with a hierarchical structure of two levels: a top-level orchestrator graph and two embedded subgraphs (work_planner and code_generator).

---

## 1. StateGraph Structure (`graph/builder.py`)

### Top-Level Graph Topology

```
START → work_planner (subgraph) → await_approval → execute_plan (code_generator subgraph)
        → await_pr_approval → END
            ↓ (rejected)           ↓ (error)
           END                     END
            ↑ (re-execute)
```

### Key Components

**Graph Definition:**
- `StateGraph(OrchestratorState)` - uses a unified state dict composition pattern
- 4 top-level nodes: `work_planner`, `await_approval`, `execute_plan`, `await_pr_approval`
- **Checkpointer:** `SqliteSaver` backed by application database (path from `get_db_path()`)
- **Entry point:** `work_planner`

**Conditional Routing:**
Three routing functions control flow:
- `_route_after_work_planner`: Skips approval if error exists
- `_route_after_approval`: Routes to execute_plan if approved
- `_route_after_pr_approval`: Routes back to execute_plan if commented (loop for incremental fixes)

**CompiledGraph Creation:**
```python
builder.compile(checkpointer=checkpointer)  # Returns a CompiledGraph ready for .invoke()
```

### Subgraph Pattern

Both subgraphs follow the same structure:
- **Work Planner:** 10 nodes (validate_input → generate_plan → validate_plan → await_workplan_clarification loop → store_plan → post_to_jira)
- **Code Generator:** 6 nodes (resolve_repo → clone_repo → run_goose → process_results → persist_results → cleanup)
- Subgraphs compile without their own checkpointer
- Embedded as single nodes in parent graph (failure → rewind to parent node level in checkpointer history)

---

## 2. Node Definition and Execution Pattern

### Node Function Signature

All nodes follow this pattern:
```python
def node_name(state: InputState) -> OutputState:
    """Read from state, execute logic, return partial state update."""
    value = state.get("key")
    # ... logic ...
    return {"key_updated": result}
```

### State Management

**Typed State Composition** (`graph/state.py`):
- Interface Segregation Principle: Each stage has focused TypedDict
- Examples:
  - `ApprovalInputState` - only keys needed by await_approval
  - `CodeGenerationOutputState` - only keys output by code_generator
  - `OrchestratorState` - superset composition of all

**State Update Semantics:**
- Nodes return **partial state dicts** (merged by LangGraph)
- Keys not in return dict are preserved
- Used for progressive state accumulation through the graph

### Node Execution Flow

**Invocation Point** (`dispatcher/commands/run_workflow.py`):
```python
workflow_id = str(uuid.uuid4())  # Shared with LangGraph thread_id
thread_config = {"configurable": {"thread_id": workflow_id}}
graph = build_orchestrator()

final_state = graph.invoke(
    {"ticket_key": ticket, "dry_run": False, "workflow_id": workflow_id},
    config=thread_config,
)
```

**Suspension/Resumption (Interrupts):**
- `await_approval` and `await_pr_approval` nodes call `interrupt(payload)`
- Graph is serialized to checkpointer and execution pauses
- CLI commands resume: `graph.invoke(None, config={"configurable": {"thread_id": workflow_id}})`
- Resume payload injected via `Command(resume={...})`

### Node Examples

**Simple Synchronous Node** (`graph/nodes/await_approval.py`):
```python
def await_approval(state: ApprovalInputState) -> ApprovalOutputState:
    # Read workflow status from DB
    workflow = get_workflow(workflow_id)

    # Print instructions to user
    click.echo("Awaiting developer approval...")

    # Suspend graph execution
    resume_payload = interrupt({"workflow_id": workflow_id})

    # Resume path - decision injected via Command
    decision = resume_payload.get("decision")

    # Update status, return state update
    update_status(workflow_id, WorkflowStatus.APPROVED)
    return {"approval_decision": "approved"}
```

**Subgraph Node** (`graph/work_planner/nodes/generate_plan.py`):
```python
def generate_plan(state: GeneratePlanInputState) -> GeneratePlanOutputState:
    # Prepare environment and logging
    lp = log_path(workflow_id, "plan")

    # Execute external process (Goose recipe)
    result = run_and_tee(cmd, log_file, env=goose_env)

    # Parse results
    work_plan_data = json.load(output_file)

    # Track LLM usage for instrumentation
    usage = aggregate_token_usage(workflow_id, "plan")
    update_usage_summary(workflow_id, "plan", usage)

    return {"work_plan_data": work_plan_data}
```

---

## 3. Existing Callbacks and Middleware Patterns

### TokenUsageLogger Pattern (`graph/litellm_callbacks.py`)

**Custom LiteLLM Callback:**
```python
class TokenUsageLogger(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        # Extract token counts from response
        # Build entry dict with workflow context
        # Append to JSONL log file (thread-safe with _WRITE_LOCK)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        # Extract exception info
        # Build failure entry
        # Append to llm_failures.jsonl
```

**Context Propagation:**
- Environment variables set in `goose_session()` context manager:
  - `NGB_WORKFLOW_ID` - injected into subprocess
  - `NGB_WORKFLOW_STAGE` - "plan", "execute", etc.
- Enables token usage tracking per workflow stage
- JSONL format for easy streaming analysis

**Integration Points:**
- Instantiated in `graph/utils.py:goose_session()`
- Passed to LiteLLM proxy configuration
- Asyncio-safe logging with threading lock

### Retry Mechanism (`graph/retry.py`)

**Checkpoint History Walking:**
```python
def find_rewind_config(graph, thread_config, parent_node):
    for snapshot in graph.get_state_history(thread_config):
        if parent_node in (snapshot.next or ()):
            return snapshot.config  # Return config BEFORE node ran
```

**State Rewind:**
- Maps failed subgraph nodes to parent node
- Clears `error` and `failed_node` from checkpointed state
- Caller re-invokes: `graph.invoke(None, config=rewind_config)`

---

## 4. Orchestration Flow Entry Point

### Dispatcher CLI (`dispatcher/run.py`)

**Main Dispatcher Flow:**
1. Parse CLI arguments (ticket, dry_run, approve-plan, reject, etc.)
2. Lazy-load command handlers only when needed (reduces startup time)
3. Dispatch to appropriate handler

**Workflow Execution Handler** (`dispatcher/commands/run_workflow.py`):
```python
def _handle_run(ticket: str, dry_run: bool):
    workflow_id = str(uuid.uuid4())
    thread_config = {"configurable": {"thread_id": workflow_id}}
    graph = common.build_orchestrator()

    try:
        final_state = graph.invoke(
            {"ticket_key": ticket, "dry_run": False, "workflow_id": workflow_id},
            config=thread_config,
        )
    except GraphInterrupt:
        # Graph hit interrupt() in await_approval node
        # Status already marked PENDING_APPROVAL in DB
        pass
    except KeyboardInterrupt:
        # Mark workflow as interrupted, clean up
        common._mark_workflow_interrupted(workflow_id, graph, thread_config)
```

**Post-Execution Hooks:**
- `common._post_execution_comment(ticket, execution_summary)` - posts to Jira ticket
- Status updates via `update_status(workflow_id, WorkflowStatus.COMPLETED)`

---

## 5. Current Node Execution Without Instrumentation

### Challenge: No Global Node Instrumentation Hook

LangGraph's architecture means:
1. **Each node is independent** - No wrapping layer exists across all nodes
2. **Nodes are black boxes** - LangGraph calls them with state, captures return value
3. **No built-in middleware pattern** - Unlike Express.js or FastAPI, no central pre/post-processor
4. **State updates are implicit** - Only visible when node returns

### Current Limitations

- Token usage tracked only at LLM call level (via TokenUsageLogger)
- No visibility into generic node execution time, input size, output size
- Error handling is per-node (each node's try/catch block)
- No centralized audit trail of state mutations

---

## 6. Instrumentation Injection Points (Without Node Modification)

### ✅ **Option 1: Stream-Based Interception** (Best for Observability)

**How it works:**
- LangGraph `CompiledGraph` supports `.stream()` and `.astream()` methods
- Returns events for each node execution with before/after state
- **Advantage:** No node code modification, full visibility into graph execution

**Implementation:**
```python
# Instead of: graph.invoke(...)
for event in graph.stream(input_state, config=thread_config):
    # event structure: {"node_name": {"messages": [...] or state_update}}
    # Intercept here for instrumentation
```

**Available Events:**
- `on_chain_start`: Before node execution
- `on_chain_end`: After node execution
- `on_chain_error`: On exception
- Custom events from nodes via `RunnableConfig`

### ✅ **Option 2: Checkpointer Wrapper** (Intercepting State)

**How it works:**
- Wrap the `SqliteSaver` checkpointer
- Intercept all state mutations at storage level
- Capture before/after state snapshots

**Implementation:**
```python
class InstrumentedCheckpointer(SqliteSaver):
    def put_checkpoint(self, config, checkpoint):
        # Log: workflow_id, node, state_delta
        super().put_checkpoint(config, checkpoint)

    def get_checkpoint(self, config):
        checkpoint = super().get_checkpoint(config)
        # Log: state_retrieved at checkpoint_id
        return checkpoint
```

**Advantages:**
- Minimal change to existing code (1-line swap in `build_orchestrator`)
- Captures all state transitions
- Natural point to log node execution timeline

### ✅ **Option 3: CompiledGraph Wrapper Function** (Decorator Pattern)

**How it works:**
- Create a wrapper function around the compiled graph
- Inject instrumentation before/after `.invoke()`

**Implementation:**
```python
def with_instrumentation(graph):
    def instrumented_invoke(input_state, config=None):
        node_start_times = {}

        # Capture execution timeline via stream
        for event in graph.stream(input_state, config):
            for node_name, data in event.items():
                if node_name not in node_start_times:
                    node_start_times[node_name] = time.time()
                    log_event("node_start", node=node_name, ...)

                if "error" in data:
                    duration = time.time() - node_start_times[node_name]
                    log_event("node_error", node=node_name, duration=duration, ...)

        return graph.invoke(input_state, config)

    return instrumented_invoke
```

**Integration point:** `dispatcher/commands/run_workflow.py` line 36

### ✅ **Option 4: Node Wrapper Factory** (Proxy Function)

**How it works:**
- Create a higher-order function that wraps nodes without modifying them
- Apply to all nodes at builder time

**Implementation:**
```python
def with_instrumentation(node_func, node_name):
    def instrumented_node(state):
        start = time.time()
        input_size = len(str(state))

        try:
            result = node_func(state)
            duration = time.time() - start
            output_size = len(str(result))

            log_event("node_complete",
                node=node_name,
                duration_ms=duration*1000,
                input_bytes=input_size,
                output_bytes=output_size
            )
            return result
        except Exception as e:
            duration = time.time() - start
            log_event("node_failed", node=node_name, duration_ms=duration*1000, error=str(e))
            raise

    return instrumented_node

# In builder:
builder.add_node("work_planner", with_instrumentation(work_planner, "work_planner"))
```

**Integration point:** `graph/builder.py` lines 83-86

### ⚠️ **Option 5: LangGraph Callbacks (Lower Priority)**

LangGraph has a callback system but it's designed for LLM/tool calls, not node execution.
- Would require patching at LangGraph library level
- Not recommended unless deep tracing is needed

---

## 7. Recommended Instrumentation Strategy

For **maximum visibility with minimal modification**, recommend **Options 2 + 3**:

1. **Option 2 (Checkpointer Wrapper):**
   - Captures state transitions at the database level
   - Enables audit trail of all state changes
   - Minimal code change (1-line in `build_orchestrator()`)
   - Non-invasive

2. **Option 3 (Stream-Based Interception):**
   - Replaces `graph.invoke()` with loop over `graph.stream()` events
   - Captures node execution timing and errors
   - Full visibility into graph execution flow
   - Change location: `dispatcher/commands/run_workflow.py` (consolidate into `common.build_orchestrator()` or create new `invoke_with_instrumentation()` wrapper)

### Implementation Roadmap

```
Step 1: Create InstrumentedCheckpointer(SqliteSaver)
        Location: graph/instrumentation.py

Step 2: Create stream-based event collector
        Location: graph/instrumentation.py

Step 3: Update build_orchestrator() to accept checkpointer=InstrumentedCheckpointer
        Location: graph/builder.py

Step 4: Update _handle_run() to use stream_with_instrumentation()
        Location: dispatcher/commands/run_workflow.py

Step 5: Add metric aggregation and reporting
        Location: graph/instrumentation.py or dispatcher/commands/common.py
```

---

## Key Architectural Insights

### State Pattern Benefits
- **Immutability**: Partial updates prevent cross-node contamination
- **Auditability**: Each node's contribution is trackable
- **Type safety**: TypedDict enforces contract between nodes

### Checkpointing Strategy
- **Resumability**: Thread-level checkpointing enables pause/resume
- **Retry capability**: Checkpoint history allows rewinding to failed node
- **Persistence**: SQLite integration keeps state across CLI invocations

### Human-in-the-Loop Architecture
- **Interrupts**: `interrupt()` suspends graph, resumes on `graph.invoke(resume=...)`
- **Command pattern**: CLI commands map to resume payloads
- **No polling**: State stored in DB, accessed on-demand

---

## Files for Reference

| Component | File(s) |
|-----------|---------|
| Top-level orchestrator graph | `graph/builder.py` |
| Orchestrator state types | `graph/state.py` |
| Work planner subgraph | `graph/work_planner/builder.py` |
| Code generator subgraph | `graph/code_generator/builder.py` |
| Token usage callback | `graph/litellm_callbacks.py` |
| Retry mechanism | `graph/retry.py` |
| Workflow execution handler | `dispatcher/commands/run_workflow.py` |
| Dispatcher entry point | `dispatcher/run.py` |
| Node patterns | `graph/nodes/*.py`, `graph/work_planner/nodes/*.py`, `graph/code_generator/nodes/*.py` |
