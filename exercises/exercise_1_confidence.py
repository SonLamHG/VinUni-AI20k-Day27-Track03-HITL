"""Exercise 1 — Confidence scoring + routing.

Build a small LangGraph that fetches a PR, analyzes it, then routes to one of
three terminal nodes by confidence. Goal: see the three branches print
different messages on different PRs.

"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from rich.console import Console

from common.github import fetch_pr
from common.llm import get_llm
from common.schemas import (
    AUTO_APPROVE_THRESHOLD,
    ESCALATE_THRESHOLD,
    PRAnalysis,
    ReviewState,
)


console = Console()


def node_fetch_pr(state: ReviewState) -> dict:
    console.print("[cyan]→ fetch_pr[/cyan]")
    with console.status("[dim]Fetching PR from GitHub...[/dim]"):
        pr = fetch_pr(state["pr_url"])
    console.print(f"  [green]✓[/green] {len(pr.files_changed)} files, head {pr.head_sha[:7]}")
    return {
        "pr_title": pr.title, "pr_diff": pr.diff,
        "pr_files": pr.files_changed, "pr_head_sha": pr.head_sha,
    }


def node_analyze(state: ReviewState) -> dict:
    console.print("[cyan]→ analyze[/cyan]")
    llm = get_llm().with_structured_output(PRAnalysis)
    with console.status("[dim]LLM thinking...[/dim]"):
        analysis = llm.invoke([
            {"role": "system", "content": (
                "You are a senior code reviewer. Read the PR diff and produce a "
                "structured review. Be specific (cite file + line where possible).\n\n"
                "CONFIDENCE CALIBRATION — be honest, do NOT default to high.\n"
                "Map your evaluation to one of these bands; pick the MIDDLE of "
                "the band, not the edge:\n"
                "  • 0.80–0.95  Mechanical / very safe — typo, dep bump, "
                "doc-only, formatting, pure rename. No new logic.\n"
                "  • 0.62–0.70  Small feature or schema addition with ONE or "
                "TWO minor open questions (intent, naming, migration order, "
                "missing test for a single helper). No security or data-loss "
                "concerns. THIS IS THE COMMON CASE — use it when nothing is "
                "actually broken but a human eye would help.\n"
                "  • 0.30–0.55  ANY of: security-sensitive code (auth, crypto, "
                "password handling, MD5/SHA1 for passwords), SQL/string-built "
                "queries, plaintext secret/token storage, network sync to "
                "external URLs, hard-coded user/account ids, or no tests for "
                "new non-trivial logic. Populate `escalation_questions` with "
                "2–4 specific questions referencing file:line.\n\n"
                "Reference points:\n"
                "  • Adding an optional `--priority` flag + an `int` field on a "
                "dataclass, with backward-compatible default → 0.65 (small "
                "feature, one open question on existing-data migration).\n"
                "  • Adding MD5 password hashing or plaintext token storage "
                "→ 0.35 (security-critical, must escalate).\n\n"
                "When uncertain between two adjacent bands, pick the band you "
                "literally fall into — do NOT subtract a buffer 'to be safe'. "
                "The routing already handles uncertainty (the 0.58–0.72 band "
                "is exactly the 'human reviews' zone). Risk factors must "
                "lower confidence, but absence-of-risk is NOT a reason to "
                "drop below 0.60."
            )},
            {"role": "user", "content": f"Title: {state['pr_title']}\nDiff:\n{state['pr_diff']}"},
        ])
    console.print(
        f"  [green]✓[/green] confidence={analysis.confidence:.0%}, "
        f"{len(analysis.comments)} comment(s)"
    )
    return {"analysis": analysis}


def node_route(state: ReviewState) -> dict:
    console.print("[cyan]→ route[/cyan]")
    c = state["analysis"].confidence
    if c >= AUTO_APPROVE_THRESHOLD:
        decision = "auto_approve"
    elif c < ESCALATE_THRESHOLD:
        decision = "escalate"
    else:
        decision = "human_approval"
    console.print(f"  [green]✓[/green] decision=[bold]{decision}[/bold] (confidence={c:.0%})")
    return {"decision": decision}


def node_auto_approve(state: ReviewState) -> dict:
    console.print("[green]✓ AUTO APPROVE[/green] — high confidence, no human needed")
    return {"final_action": "auto_approved"}


def node_human_approval(state: ReviewState) -> dict:
    console.print("[yellow]✓ HUMAN APPROVAL[/yellow] — placeholder, exercise 2 will pause here")
    return {"final_action": "pending_human_approval"}


def node_escalate(state: ReviewState) -> dict:
    console.print("[red]✓ ESCALATE[/red] — placeholder, exercise 3 will ask the reviewer questions")
    return {"final_action": "pending_escalation"}


def build_graph():
    g = StateGraph(ReviewState)
    for name, fn in [
        ("fetch_pr", node_fetch_pr),
        ("analyze", node_analyze),
        ("route", node_route),
        ("auto_approve", node_auto_approve),
        ("human_approval", node_human_approval),
        ("escalate", node_escalate),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "fetch_pr")
    g.add_edge("fetch_pr", "analyze")
    g.add_edge("analyze", "route")
    g.add_conditional_edges(
        "route", lambda s: s["decision"],
        {
            "auto_approve": "auto_approve",
            "human_approval": "human_approval",
            "escalate": "escalate",
        },
    )
    g.add_edge("auto_approve", END)
    g.add_edge("human_approval", END)
    g.add_edge("escalate", END)
    return g.compile()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", required=True)
    args = parser.parse_args()

    console.rule("[bold]Exercise 1 — confidence routing[/bold]")
    console.print(f"[dim]PR: {args.pr}[/dim]\n")

    app = build_graph()
    final = app.invoke({"pr_url": args.pr})

    console.rule("Final")
    console.print(f"confidence = {final['analysis'].confidence:.0%}")
    console.print(f"action     = {final.get('final_action')}")


if __name__ == "__main__":
    main()
