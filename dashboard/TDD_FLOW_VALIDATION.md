# TDD Flow Validation

Companion to `TDD_EXTRACTION_SPEC.md`. That document covers *what* gets
extracted — behaviours, categories, positive/negative/edge variants. This one
covers *whether the result can actually be run*.

---

## 1. The defect this fixes

A checkpoint can be a faithful reading of the source document and still be
impossible to execute:

> **Verify that extracted skills regenerate when the Job Description changes.**

That is exactly what the requirements say. But a tester handed that sentence
has no session, no workspace, and no job — there is nowhere to begin. The
extractor cannot see the problem, because the section it read does not mention
the six screens that come before it.

The cause is structural, not a prompt-quality issue:

**A requirements document is organised by subject. Execution is organised by
state.** The extractor works section by section, so it inherits the document's
organisation and loses the product's.

No amount of prompt tuning recovers a sequence the source text never states.
The sequence has to be supplied separately, and checkpoints have to be held
against it.

---

## 2. What a flow model is

A per-project description of the states a user passes through, and what each
one requires. Minimum shape:

```json
{
  "states": [
    {"id": "S00_ANON",           "requires": []},
    {"id": "S01_AUTHENTICATED",  "requires": ["S00_ANON"]},
    {"id": "S02_WORKSPACE",      "requires": ["S01_AUTHENTICATED"]}
  ]
}
```

Optional per state:

| Key | Purpose |
|---|---|
| `name` / `description` | Short human label, included in the prompt |
| `pages` | Page names that map to this state. Used to anchor a checkpoint whose `page` is set but whose `precondition_state` is not. A **lookup**, not an inference — nothing is guessed |
| `locked_behaviours` | Behaviours that become permanently unavailable from this state onward. Cumulative; never released |

Optional at the top level: `entry_state`. When omitted, the entry state is the
single state with no `requires`. **Two or more such states means the model does
not say where a run begins**, and validation is skipped rather than picking one
— choosing arbitrarily would silently change which checkpoints validate.

---

## 3. Where the flow model comes from

`flow_validation.get_flow_model(session, project_id)`.

Today it resolves only the `TDD_FLOW_MODEL_PATH` environment variable, so every
project returns `None` and the feature is inert. The memory/intelligence layer
replaces the body of that one function. Its signature deliberately mirrors
`ui_inventory.get_inventory_text(session, project_id)`, so the worker's call
site is already correct and will not move.

**The flow model is authored, not generated.** Nothing can know that a Job
Description locks on Save except someone who has used the product. That
knowledge is the input; everything around it is machinery.

---

## 4. How it runs

Stage 4d, after dedupe — so a checkpoint is anchored once rather than once per
near-duplicate — and after repair, so repaired variants are anchored too.

```
zone → extract → repair → cap → dedupe → [flow] → scorecard
```

**On a checkpoint that anchors cleanly**, two fields are added:

| Field | Meaning |
|---|---|
| `precondition_state` | The state at which its first instruction becomes possible |
| `setup_path` | Ordered states from the entry state to it — literally what a runner must do before step 1 means anything |

**On a checkpoint that does not**, the content is left alone and the checkpoint
is flagged:

```
review_status = "needs_design_flow"
review_reason = "<code> — this checkpoint <explanation>."
```

### Failure codes

| Code | Meaning |
|---|---|
| `flow:no_precondition_state` | Names no state, and no `pages` entry matched |
| `flow:unknown_state` | Names a state that is not in the model |
| `flow:unreachable_state` | Names a state not reachable from the entry state, or reachable only through a cycle |
| `flow:asserts_locked_behaviour` | Claims a behaviour succeeds that is locked by the time this state is reached |

A frequency count over `review_reason` tells you which prompt rule to tighten
next. If `no_precondition_state` dominates, the model is ignoring the rule. If
`asserts_locked_behaviour` dominates, the source document almost certainly
contradicts itself about that behaviour.

---

## 5. Two rules that are easy to get wrong

**Locks are cumulative and never released.** A behaviour locked at state 6 is
still locked at state 11. A checkpoint anchored late inherits every earlier
lock.

**Asserting that a locked behaviour is blocked is the correct test.** Only a
checkpoint claiming a locked behaviour *succeeds* is flagged. These are
opposites and must not be conflated:

| Expected result | Verdict |
|---|---|
| "The updated description is saved successfully" | flagged |
| "The system blocks the edit and shows an error" | accepted |

---

## 6. Nothing is ever dropped

An unrunnable checkpoint is annotated and kept, exactly as the testability gate
records what it excluded rather than deleting it. A human decides.

An existing `review_status` is never overwritten. "The document did not specify
this well enough to execute" outranks "and it also has no reachable starting
point" — the first reason is the one a reviewer needs.

---

## 7. Fails open, always

Flow validation is an advisory pass over work that has already succeeded. A
malformed model, a cycle, a missing file, an unexpected exception — none may
cost an extraction. Every path returns the checkpoints it was given and reports
that it did nothing.

**With no flow model, the pipeline behaves exactly as it did before this
feature existed.** The extraction prompt is byte-identical; the checkpoint dict
gains no keys; the scorecard gains no `flow` key. That last point matters: the
key is *absent* rather than zero, so "never flow-checked" reads differently
from "checked, and everything anchored".

---

## 8. Configuration

| Variable | Default | Effect |
|---|---|---|
| `TDD_FLOW_VALIDATION` | enabled | Set `0` to disable the stage entirely |
| `TDD_FLOW_MODEL_PATH` | unset | Absolute path to a flow model JSON, applied to every project. Escape hatch for testing before the memory layer lands — **not** a production configuration |

Opt-out convention, matching every other `TDD_*` flag: unset means enabled.

---

## 9. Trying it before the memory layer exists

```bash
export TDD_FLOW_MODEL_PATH=/abs/path/to/flow_model.json
# re-ingest a SOW; the Skills & TDDs panel gains a "flow" stage
```

Then read the scorecard's `flow` block:

```json
{"states_in_model": 13, "entry_state": "S00_ANON",
 "anchored": 41, "unanchored": 7,
 "by_state": {"S05_SKILLS_EXTRACTED": 10},
 "by_reason": {"flow:no_precondition_state": 7}}
```

`unanchored` is the number to watch. High on the first run is expected and is
the point — those are the checkpoints that would otherwise have failed at
runtime for a reason nobody could see.

---

## 10. Scope

**In:** anchoring a checkpoint to a reachable state, and flagging when it
cannot be.

**Out:** whether the *expected result* is correct, whether the behaviour exists
in the product, and generating the flow model. The first two belong to
extraction and to review; the third is the memory layer's job.
