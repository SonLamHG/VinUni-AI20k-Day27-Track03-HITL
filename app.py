"""Exercise 5 — Streamlit approval UI for the HITL PR review agent.

Run with:
    uv run streamlit run app.py

Wraps the LangGraph built in exercise 4 in a web UI that adapts to the
confidence bucket of each PR.

Routing thresholds (common/schemas.py):
    > 72%        auto_approve     UI shows a success card; reviewer does nothing
    58 – 72%     human_approval   UI shows Approve / Reject / Edit buttons
    <  58%       escalate         UI shows a question form for the reviewer
"""

from __future__ import annotations

import asyncio
import uuid

import aiosqlite
import streamlit as st
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from common.db import db_path, db_conn
from exercises.exercise_4_audit import build_graph


load_dotenv()


# ─── Session state ─────────────────────────────────────────────────────────
for key, default in [
    ("thread_id", None),
    ("pr_url", ""),
    ("interrupt_payload", None),
    ("final", None),
    ("pending_resume", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ─── Page setup ────────────────────────────────────────────────────────────
st.set_page_config(page_title="HITL PR Review", layout="wide")
st.title("HITL PR Review Agent")


# ─── Sidebar — recent sessions ─────────────────────────────────────────────
async def _recent_sessions(limit: int = 15) -> list[dict]:
    async with db_conn() as conn:
        async with conn.execute(
            """
            SELECT thread_id,
                   pr_url,
                   MIN(timestamp)  AS started,
                   MAX(timestamp)  AS last_event,
                   MAX(risk_level) AS worst_risk,
                   COUNT(*)        AS events
              FROM audit_events
             GROUP BY thread_id, pr_url
             ORDER BY MAX(timestamp) DESC
             LIMIT ?
            """,
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("Recent sessions")
        try:
            sessions = asyncio.run(_recent_sessions())
        except Exception as e:
            st.caption(f"(no audit_events yet — {e})")
            return
        if not sessions:
            st.caption("(no sessions yet — run a review below)")
            return
        for s in sessions:
            short = s["thread_id"][:8]
            pr = s["pr_url"].rsplit("/", 2)
            pr_short = "/".join(pr[-3:]) if len(pr) >= 3 else s["pr_url"]
            label = f"{short}  ·  {pr_short}"
            badge = {"low": "🟢", "med": "🟡", "high": "🔴"}.get(s["worst_risk"], "·")
            if st.button(f"{badge} {label}", key=f"replay-{s['thread_id']}"):
                st.session_state.thread_id = s["thread_id"]
                st.session_state.pr_url = s["pr_url"]
                st.session_state.interrupt_payload = None
                st.session_state.final = None
                st.session_state.pending_resume = None
                st.rerun()
            st.caption(f"{s['events']} events · {s['last_event'][:19]}")


_render_sidebar()


# ─── Top form — start a new review ─────────────────────────────────────────
with st.form("start"):
    pr_url = st.text_input(
        "PR URL", value=st.session_state.pr_url,
        placeholder="https://github.com/VinUni-AI20k/PR-Demo/pull/1",
    )
    submitted = st.form_submit_button("Run review")


# ─── Renderers per interrupt kind ──────────────────────────────────────────
def render_approval_card(payload: dict) -> dict | None:
    """58–72% bucket: show the LLM review + 3 buttons. Return resume dict or None."""
    conf = payload["confidence"]
    st.subheader(f"Approval requested — confidence {conf:.0%}")
    st.caption(payload["confidence_reasoning"])
    st.markdown(payload["summary"])

    for c in payload.get("comments", []):
        st.markdown(f"- **[{c['severity']}]** `{c['file']}:{c.get('line') or '?'}` — {c['body']}")

    with st.expander("Diff"):
        st.code(payload.get("diff_preview", ""), language="diff")

    feedback = st.text_input("Feedback (optional)", key="approval_feedback")
    col1, col2, col3 = st.columns(3)
    if col1.button("Approve", type="primary", key="btn_approve"):
        return {"choice": "approve", "feedback": feedback}
    if col2.button("Reject", key="btn_reject"):
        return {"choice": "reject", "feedback": feedback}
    if col3.button("Edit", key="btn_edit"):
        return {"choice": "edit", "feedback": feedback}
    return None


def render_escalation_card(payload: dict) -> dict | None:
    """< 58% bucket: show risk factors + question form. Return {question: answer} or None."""
    conf = payload["confidence"]
    st.subheader(f"Strong escalation — confidence {conf:.0%}")
    st.caption(payload["confidence_reasoning"])
    if payload.get("risk_factors"):
        st.error("Risks: " + " · ".join(payload["risk_factors"]))
    st.markdown(payload["summary"])

    with st.form("escalation_form"):
        answers: dict[str, str] = {}
        for i, q in enumerate(payload["questions"]):
            answers[q] = st.text_input(q, key=f"esc_q_{i}")
        submit = st.form_submit_button("Submit answers")
    if submit:
        return answers
    return None


# ─── Drive the graph ───────────────────────────────────────────────────────
async def run_graph(pr_url: str, thread_id: str, resume_value=None):
    """Invoke the graph once. Returns the final result or {'__interrupt__': ...}."""
    async with AsyncSqliteSaver.from_conn_string(db_path()) as cp:
        await cp.setup()
        app = build_graph(cp)
        cfg = {"configurable": {"thread_id": thread_id}}
        if resume_value is None:
            return await app.ainvoke(
                {"pr_url": pr_url, "thread_id": thread_id}, cfg,
            )
        return await app.ainvoke(Command(resume=resume_value), cfg)


# ─── Main flow ─────────────────────────────────────────────────────────────
def _apply_result(result: dict) -> None:
    if "__interrupt__" in result:
        st.session_state.interrupt_payload = result["__interrupt__"][0].value
        st.session_state.final = None
    else:
        st.session_state.interrupt_payload = None
        st.session_state.final = result


if submitted and pr_url:
    st.session_state.pr_url = pr_url
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.interrupt_payload = None
    st.session_state.final = None
    st.session_state.pending_resume = None

    with st.spinner("Fetching PR + asking the LLM..."):
        result = asyncio.run(run_graph(pr_url, st.session_state.thread_id))
    _apply_result(result)


# A pending resume was queued in the previous run (e.g. user clicked Approve)
if st.session_state.pending_resume is not None:
    resume_value = st.session_state.pending_resume
    st.session_state.pending_resume = None
    with st.spinner("Resuming..."):
        result = asyncio.run(run_graph(
            st.session_state.pr_url, st.session_state.thread_id,
            resume_value=resume_value,
        ))
    _apply_result(result)
    st.rerun()


payload = st.session_state.interrupt_payload
if payload is not None:
    kind = payload["kind"]
    answer = (
        render_approval_card(payload) if kind == "approval_request"
        else render_escalation_card(payload)
    )
    if answer is not None:
        st.session_state.pending_resume = answer
        st.rerun()


# Render final state, if reached
if st.session_state.final is not None:
    final = st.session_state.final
    action = final.get("final_action", "?")
    if action.startswith("auto") or action.startswith("committed"):
        st.success(f"✓ {action} — comment posted to {st.session_state.pr_url}")
    elif action == "rejected":
        st.warning("Rejected — no comment posted")
    else:
        st.info(f"final_action = {action}")
    if "analysis" in final:
        a = final["analysis"]
        with st.expander("Final analysis"):
            st.metric("Confidence", f"{a.confidence:.0%}")
            st.markdown(a.summary)
            for c in a.comments:
                st.markdown(f"- **[{c.severity}]** `{c.file}:{c.line or '?'}` — {c.body}")
    st.caption(
        f"thread_id = {st.session_state.thread_id}  ·  replay: "
        f"`uv run python -m audit.replay --thread {st.session_state.thread_id}`"
    )
