# HITL PR Review Lab — Execution Design

Date: 2026-05-15
Goal: complete all 5 exercises in `exercises/` + `app.py` with full marks against the rubric implied by `README.md`.

## Rubric mapping

| Exercise | Pass criterion |
|----------|----------------|
| E1 | PR #1 and PR #2 print **different** branches (`human_approval` vs `escalate`) |
| E2 | `interrupt()` pauses the graph; resume with `Command(resume=...)` posts a comment |
| E3 | PR #2 enters `escalate`, LLM emits specific `escalation_questions`, `synthesize` produces refined review |
| E4 | `audit_events` has ≥ 1 row per node per run; `replay` reads timeline; `risk_level='high' AND decision='auto'` returns 0 rows |
| E5 | `streamlit run app.py` works for all 3 confidence buckets; sidebar lists recent sessions |

## Phase 0 — Setup

User obtains an OpenRouter key and a GitHub PAT (`public_repo` scope), pastes both into `.env`. Run `uv sync` and smoke-test connectivity to both APIs.

## Phase 1 — Exercise 1 (confidence routing)

`exercises/exercise_1_confidence.py`. Fill `node_analyze` (call `get_llm().with_structured_output(PRAnalysis)`), `node_route` (read confidence, return `{"decision": ...}`), and `build_graph` (add nodes, edges, conditional edges from `route` → 3 terminals → END).

Verify: PR #1 → `human_approval`; PR #2 → `escalate`.

## Phase 2 — Exercise 2 (HITL with interrupt)

`exercises/exercise_2_hitl.py`. Fill `node_human_approval` with `interrupt(payload)` (kind="approval_request"); add `checkpointer=MemorySaver()` in compile; add resume loop in `main()` that consumes `__interrupt__` and re-invokes with `Command(resume=...)`.

Verify: PR #1 pauses; choosing `approve` posts a comment to the PR.

## Phase 3 — Exercise 3 (escalation + Q&A)

`exercises/exercise_3_escalation.py`. Augment the analyze prompt to populate `escalation_questions` when confidence < 0.60. Fill `node_escalate` with `interrupt({"kind": "escalation", ...})` carrying the questions. Fill `node_synthesize` to re-invoke the LLM with diff + initial analysis + Q&A → refined `PRAnalysis`. Wire `escalate → synthesize → commit`.

Verify: PR #2 shows the yellow question panel; refined review is posted after answers.

## Phase 4 — Exercise 4 (audit trail)

`exercises/exercise_4_audit.py`. Implement `audit()` body (one call to `write_audit_event`). Emit one `AuditEntry` per node:
- `analyze`, `route`, `commit`, `auto_approve`, `synthesize`: one entry each
- `human_approval` and `escalate`: TWO entries each (before interrupt with `decision="pending"`; after resume with the human's outcome)

Each `execution_time_ms` measured with `time.monotonic()`. Use `risk_level_for(confidence)`. `reviewer_id` from `os.environ.get("GITHUB_USER")` on HITL steps.

Verify: `uv run python -m audit.replay --thread <id>` prints full timeline.

## Phase 5 — Exercise 5 (Streamlit UI)

`app.py`. Import `build_graph` from exercise 4. Implement:
- `run_graph` to invoke / resume the graph with `AsyncSqliteSaver`
- `render_approval_card` with 3 buttons (Approve / Reject / Edit) returning a resume dict via `st.session_state`
- `render_escalation_card` with a form of `st.text_input` per question
- Sidebar listing recent sessions via an aiosqlite query against `audit_events`

`streamlit>=1.40.0` is already in `pyproject.toml`.

Verify: 3 PR runs cover all three buckets; sidebar populated.

## Phase 6 — Final verification

Run `audit.replay --list` (≥ 3 sessions), spot-check one full thread, query `SELECT * FROM audit_events WHERE risk_level='high' AND decision='auto'` (must be empty), and view the posted comments on GitHub.

## Risks

- LLM may be overconfident on PR #2 → temporarily raise `ESCALATE_THRESHOLD` to 0.70 if needed.
- Streamlit button clicks rerun the script → persist the chosen action in `st.session_state` and call `st.rerun()` after resume.
- Side effects (posting comments) live in `node_commit`, never in nodes that call `interrupt()`, so resumes do not duplicate.

## Commit cadence

One commit per exercise (5 commits), preceded by a setup commit for `.env.example` / docs if needed.
