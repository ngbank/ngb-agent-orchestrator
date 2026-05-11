# How LLM Agents Work

This document explains the mechanics of how Goose (and LLM agents in general) execute
tasks. It is anchored in the problems we encountered while building the execute recipe
for the NGB Agent Orchestrator.

---

## The Core Loop

An LLM agent is not a program that runs your prompt top to bottom. It is a loop where
the LLM is called repeatedly, each time with the full conversation history appended.

```
history = [system_prompt + recipe instructions, initial user message]

while True:
    response = llm.complete(history)

    if response.has_tool_calls:
        for tool_call in response.tool_calls:
            result = execute(tool_call)   # shell, write_file, etc.
            history.append(tool_call)
            history.append(result)
        # Loop — LLM sees the results and decides what to do next

    else:
        # Text-only response (e.g. "Done.", "All set!", "Step complete.")
        history.append(response.text)
        exit()   # ← session ends
```

The LLM has no memory between calls. Its only context is the conversation history that
is re-sent on every turn. The "agent" is the loop that manages that history and decides
what to do with each response.

---

## How a Turn Works

Each iteration of the loop is called a **turn**. A turn looks like this:

```
Turn N:
  Input to LLM : [everything that has happened so far]
  Output       : tool_call OR text

  If tool_call : Goose executes it, appends result → Turn N+1
  If text      : session exits (by default)
```

The LLM tends to do **one logical step per turn** — especially when the prompt has
numbered steps. After each step completes successfully, the LLM pattern-matches to
"task complete" and emits a text response rather than chaining into the next step.

### Example — what went wrong with the execute recipe

```
Turn 1: LLM → shell("get_developer_rules")   [tool call, loops]
Turn 2: LLM → shell("cat workplan.json")      [tool call, loops]
Turn 3: LLM → shell("git checkout -b feat/…") [tool call, loops]

Shell result: "Switched to a new branch 'feature/AOS-64+...'"

Turn 4: LLM → "Done."                         [TEXT ONLY — session exits]
```

The LLM saw a clean success message from the branch creation, pattern-matched it to
"milestone complete", and produced a text-only reply. Goose had no mechanism to reject
that as a valid ending, so the session exited — with no code written, no commit, no PR.

---

## The Exit Problem

This is the fundamental tension in autonomous agents:

- In an **interactive session**, a text-only "Done." is fine — a human reads it and
  says "keep going".
- In an **autonomous loop**, it is fatal — there is no human to push back.

Prompting alone ("never say done without a tool call") is unreliable because the LLM
will always find a natural-feeling stopping point. It is not ignoring instructions; it
is doing what LLMs do — generating the most probable next token given the context.

---

## The Structural Fix: `response` Schema

Goose supports a `response:` block in recipe YAML that defines a required JSON schema.
When this is present, Goose registers a special `final_output` tool and changes the
loop:

```
history = [system_prompt + recipe instructions, initial user message]
available_tools = [...existing_tools..., final_output_tool]

while True:
    response = llm.complete(history, tools=available_tools)

    if response.has_tool_calls:
        for tool_call in response.tool_calls:
            if tool_call.name == "final_output":
                result = tool_call.arguments   # structured JSON
                exit(result)                   # ONLY valid exit
            else:
                result = execute(tool_call)
                history.append(tool_call)
                history.append(result)

    else:
        # Text-only "Done." — Goose intercepts it
        history.append(response.text)
        history.append("Please provide your final output using the final_output tool.")
        # Loop continues — LLM is forced to keep going
```

The LLM has no valid exit other than calling `final_output(...)`. Any "Done." pause
triggers an automatic continuation message and the loop resumes. This is a
**mechanical constraint**, not a prompting strategy.

### The Goose source (crates/goose/src/agents/agent.rs)

```rust
if no_tools_called {
    match final_output {
        Some(None) => {
            // final_output tool defined but not yet called
            // → inject continuation, keep looping
            let message = Message::user()
                .with_text(FINAL_OUTPUT_CONTINUATION_MESSAGE);
            yield AgentEvent::Message(message);
        }
        Some(Some(output)) => {
            // final_output was called → clean exit
            exit_chat = true;
        }
        None => {
            // No response schema → run retry logic, probably exit
            exit_chat = true;
        }
    }
}
```

---

## Implications for Recipe Design

| Concern | Without `response` schema | With `response` schema |
|---|---|---|
| Session ends on text reply | Yes — any turn | No — only `final_output` call |
| Reliable multi-step tasks | Unreliable | Reliable |
| Exit mechanism | Any text-only response | `final_output(structured_json)` |
| Mid-task "Done." pause | Fatal | Continuation injected, task resumes |

### Practical rules

1. **Any recipe with more than ~3 sequential steps should define a `response` schema.**
   The more steps, the more natural pause points exist, and the higher the chance the
   LLM exits early.

2. **The `response` schema should match the output the orchestrator already expects.**
   In our case, the execute recipe's final output is the execution summary JSON, so the
   schema mirrors that structure exactly.

3. **The final step in the prompt should tell the agent to call `final_output`.**
   Even though Goose enforces it mechanically, making it explicit in the prompt aligns
   the LLM's intent with the required action.

4. **Prompting ("never stop!") is a supplement, not a substitute.** Prompt instructions
   reduce the frequency of premature exits but cannot eliminate them. The `response`
   schema is the only reliable structural guarantee.

---

## max_turns

Goose also supports `max_turns` in the recipe settings (default: 1000). This is a
safety ceiling — it prevents runaway loops — but it is not a substitute for the
`response` schema. A session can still exit after 3 turns if the LLM produces a
text-only response.

```yaml
settings:
  max_turns: 100   # safety ceiling, not an exit mechanism
```

---

## Summary

| Concept | Description |
|---|---|
| Turn | One LLM call + tool execution cycle |
| Tool call | The only way to keep the loop alive |
| Text-only response | Triggers exit (unless `response` schema is defined) |
| `response` schema | Registers `final_output` tool; only valid exit |
| Continuation message | Auto-injected by Goose when text-only + schema defined |
| `max_turns` | Safety ceiling, not an exit mechanism |
