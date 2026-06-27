# Best Practices for Structuring Coding Tasks for Autonomous Agents

This document synthesises research findings from academic and industry sources on how to structure
prompts, tools, and task decomposition for autonomous coding agents. It directly informs the design
of `recipes/generate.yaml` and any future Goose recipes in this project.

---

## 1. The Agent-Computer Interface (ACI)

The Princeton / Stanford SWE-agent team coined the term **Agent-Computer Interface (ACI)** to
describe the set of tools and interaction format that allows an agent to interact with a
computer-based environment. Their core finding:

> "Just like how typical language models require good prompt engineering, good ACI design leads to
> much better results when using agents. A baseline agent without a well-tuned ACI does much worse
> than SWE-agent."

### 1.1 Proven ACI Design Decisions (SWE-bench validated)

| Design Decision | Why It Works |
|---|---|
| **File viewer shows 100 lines per turn** | Full-file reads flood context. Bounded views force the agent to work section-by-section, reducing noise. |
| **Inline linter rejects syntactically invalid edits** | Keeps the agent in a tight read→write→verify loop rather than accumulating silent failures. |
| **Search returns file names only** | Showing match context "proved too confusing." Listing filenames drives the agent to open the right file deliberately. |
| **Empty command output → explicit message** | Prevents the agent from stalling after silent successes. "Your command ran successfully and produced no output." |

**Relevance to this project:** The execute recipe currently allows compound shell commands that
`cat`/`sed`/`rg` many files in a single turn. This is the opposite of bounded context loading and
directly contributed to the AOS-81 failure.

---

## 2. Every Turn Must Produce Grounded Evidence

From Anthropic's guidelines on building effective agents:

> "During execution, it is crucial for the agents to gain ground truth from the environment at each
> step (such as tool call results or code execution) to assess its progress."

A **text-only turn** (narration, planning summary, progress update) produces no environmental
feedback. It is pure agent-to-self communication with no tool call, which means:

1. The agent is not learning anything new from the environment.
2. In Goose, a turn with zero tool calls while a `response:` schema is registered triggers the
   `FINAL_OUTPUT_CONTINUATION_MESSAGE` injection, prompting the model to call `final_output`
   immediately — often interpreted as an abort signal.

**Rule of thumb:** Every turn must end with at least one tool call. Design the recipe so each step
has a clear required tool interaction (read, write, shell command, MCP call) that the agent must
perform before moving on.

---

## 3. Prompt Chaining Over Open-Ended Instruction

Anthropic's guidance on agentic workflows:

> "Prompt chaining is ideal for situations where the task can be easily and cleanly decomposed into
> fixed subtasks. The main goal is to trade off latency for higher accuracy, by making each LLM
> call an easier task."

For implement-a-WorkPlan tasks (where subtasks, files, and acceptance criteria are all known
upfront), this means:

- **Do not** issue a single long-context session that reads everything then implements everything.
- **Do** structure the recipe as a repeated loop: *read task N context* → *implement task N* →
  *verify task N* → *advance to task N+1*.

The per-task loop pattern:
```
for each task in WorkPlan.tasks:
    read files_likely_affected for this task only
    implement the described change
    run tests / linter if fast
    continue
```

This keeps the active context small, ensures every read is immediately followed by a write, and
makes it impossible to exhaust the turn budget on context-loading alone.

---

## 4. Minimal, Targeted Context Loading

From the aider project's documented best practices:

> "Just add the files that need to be changed to the chat. Don't add lots of files — too much
> irrelevant code will distract and confuse the LLM."

> "Break your goal down into bite sized steps. Do them one at a time."

Applied to recipe design:

- Read only the `files_likely_affected` for the current task, not every file in the repo.
- Drop context from completed tasks before starting the next one (in a multi-session design).
- Avoid "reconnaissance reads" — reading files speculatively before knowing whether they need
  changing. Read a file only when you are about to act on it.

---

## 5. Tool Design Quality Matters as Much as Prompt Quality

Anthropic's appendix on ACI design:

> "One rule of thumb is to think about how much effort goes into human-computer interfaces (HCI),
> and plan to invest just as much effort in creating good agent-computer interfaces (ACI)."

Specific tool-engineering guidance:

- **Give the model enough context in the tool description to use it correctly.** A tool with a
  vague description is a footgun.
- **Prefer absolute paths over relative paths.** Using `cd ... && command` compound shells makes
  all subsequent paths ambiguous and is a common source of silent errors.
- **Match the format to natural language.** Don't impose structured output overhead (XML, line
  counts, custom delimiters) unless required. Keep tool output close to how a developer would
  narrate the result.

---

## 6. Framework-Specific Constraints (Goose)

When using a Goose recipe with a `response:` JSON schema, Goose registers a `recipe__final_output`
tool and sets it as the mandatory session-exit mechanism. The relevant source is
`crates/goose/src/agents/final_output_tool.rs` and `agent.rs` in the
[block-goose/goose](https://github.com/block/goose) repository.

**The injection mechanism:**

```rust
if no_tools_called {
    match final_output {
        Some(None) => {
            // final_output registered but not yet called
            let message = Message::user().with_text(FINAL_OUTPUT_CONTINUATION_MESSAGE);
            // injects: "You MUST call the `final_output` tool NOW..."
        }
    }
}
```

**Consequence:** Any text-only turn (zero tool calls) in a recipe with a `response:` schema is
fatal. The model receives a message that it typically interprets as an abort signal and immediately
calls `final_output` with `status: failed` — even if implementation is incomplete.

`response:` is designed for short, one-shot recipes (classifiers, structured Q&A) where the model
should not ramble — the injection nudges it back to producing schema-conformant output. It is
actively hostile to long-horizon coding work, where text-only "thinking" turns are normal and
expected between tool calls (deciding the next file to edit, planning a refactor, reading then
writing).

**Adopted mitigation (AOS-87): do not use `response:` for autonomous coding recipes.**

Both `recipes/generate.yaml` and `recipes/plan.yaml` were originally written with `response:`
schemas, but the dispatcher reads structured results from a file (`output_path`), never from
Goose's `final_output` payload. The `response:` block was therefore redundant — its only runtime
effect was enabling the injection mechanism that killed every long execute run.

Without `response:` registered, Goose treats text-only turns benignly: the assistant message is
recorded and the loop continues to the next turn. The session ends when the model stops emitting
tool calls (typically because the recipe instructs it to stop after writing the summary file).

**Design rules for recipes that need to run autonomously:**

1. **Do not declare a `response:` schema.** Have the recipe write results to a file and have the
   caller read that file.
2. **Make the session end condition explicit and tool-anchored.** End the recipe with: "write the
   summary JSON to `{{ output_path }}`, verify it exists, then stop emitting tool calls."
3. **Keep prose instructions clear and brief.** Without injection pressure, the model does not need
   lockstep diary writes or text-only-turn bans — those existed solely to defeat `response:`.
4. **`response:` is still appropriate for short structured-output recipes** (classifiers, extractors,
   summarizers) where the model should produce one structured answer and exit.

---

## 7. Summary of Recommended Practices

| Practice | Source | Current Recipe Status |
|---|---|---|
| Bounded file reads (≤100 lines per turn) | SWE-agent paper | ❌ Not enforced |
| Inline linting on every edit | SWE-agent paper | ✅ Via pre-commit hooks at commit time |
| Explicit empty-output messages | SWE-agent paper | N/A (shell handles this) |
| Per-task read→implement loop | Anthropic + aider | ✅ Step 4 enforces per-task ordering |
| Minimal targeted context loading | aider | ✅ `files_likely_affected` is authoritative; broad searches banned |
| Absolute paths over `cd &&` compounds | Anthropic ACI | ✅ Compound patterns banned |
| Omit `response:` schema for autonomous recipes | Goose source | ✅ Removed in AOS-87 |
| Tool-anchored session end ("write file, then stop") | Goose source | ✅ Both recipes end on file-verification |

---

## 8. References

| Source | URL | Key Contribution |
|---|---|---|
| SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering (Yang et al., 2024) | https://arxiv.org/abs/2405.15793 | ACI design; bounded file views; inline linting; SWE-bench evaluation |
| SWE-agent ACI documentation | https://swe-agent.com/latest/background/aci/ | Four concrete ACI decisions proven on SWE-bench |
| mini-SWE-agent | https://mini-swe-agent.com/latest/ | Simplified 100-line agent achieving >74% on SWE-bench verified |
| Anthropic: Building Effective Agents | https://www.anthropic.com/engineering/building-effective-agents | Prompt chaining; ACI investment; grounded evidence per turn |
| aider: Tips for AI pair programming | https://aider.chat/docs/usage/tips.html | Minimal context; bite-sized steps; plan-then-execute |
| Goose agent source — final_output_tool.rs | https://github.com/block/goose/blob/main/crates/goose/src/agents/final_output_tool.rs | `FINAL_OUTPUT_CONTINUATION_MESSAGE` injection mechanism |
| Goose agent source — agent.rs | https://github.com/block/goose/blob/main/crates/goose/src/agents/agent.rs | `reply_internal` loop; zero-tool-call detection |
