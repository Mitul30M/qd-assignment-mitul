"""LangGraph conversation flow: triage -> safety -> claims -> respond.

- One StateGraph per process; nodes are the flow stages (deterministic except the
  bounded extraction call inside triage).
- PostgresSaver checkpointer (same Postgres) persists turn state per rider thread,
  so conversation continuity survives restarts and is auditable.
- Money authority stays in claims.py/payments.py; the model only parses text.
"""

from __future__ import annotations

import datetime

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from . import extraction, issues, replies, tools
from .flow import (
    _IDENTITY_RE,
    _INJECT_RE,
    _PUSHBACK_RE,
    EMPTY_CLAIM,
    _clean,
    _finish,
    _handle_claim_verdict,
    _last_issue,
    _no_claim_reply,
    _record_ops,
)

# Re-exported for main.py to build one saver per process.
__all__ = ["build_graph", "handle_message_graph", "make_checkpointer"]


class TurnState(TypedDict, total=False):
    rider_id: str
    message_id: str
    text: str
    received_at: str
    received_day: str
    convo: dict
    triage: dict
    claims: list
    segments: list
    outcomes: list
    early_reply: str
    reply: str


def make_checkpointer(database_url: str) -> PostgresSaver:
    """One checkpointer per process; setup() is idempotent."""
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    saver = PostgresSaver(conn)
    saver.setup()
    return saver


def build_graph(conn, checkpointer: PostgresSaver | None = None):
    graph = StateGraph(TurnState)

    # ---- node: triage ----
    def triage(state: TurnState) -> TurnState:
        received_day = tools.received_day_of(state["received_at"])
        convo = issues.current_conversation(conn, state["rider_id"], received_day)
        try:
            tri = extraction.extract_triage(state["text"], received_day)
        except Exception as exc:  # provider failure must never 5xx
            tri = extraction.fallback_triage(state["text"], received_day)
            from app.db import trace

            trace(conn, state["rider_id"], "error", "extraction",
                  {"message_id": state["message_id"]},
                  {"error": str(exc)[:300], "fallback": True})
        from app.db import trace

        trace(conn, state["rider_id"], "decision", "extract_entities",
              {"message_id": state["message_id"]}, {"triage": _clean(tri)})
        return {"received_day": received_day.isoformat(), "triage": tri,
                "convo": convo, "segments": [], "outcomes": []}

    # ---- node: safety ----
    def safety(state: TurnState) -> TurnState:
        tri = state["triage"]
        text = state["text"]
        rider_id = state["rider_id"]
        mention = tri.get("self_rider_mention")
        ident = _IDENTITY_RE.search(text)
        if (mention and mention != rider_id) or (ident and ident.group(1).upper() != rider_id):
            named = mention or ident.group(1).upper()
            _record_ops(conn, rider_id, "escalation", None,
                        f"identity guard: mentioned {named}")
            return {"early_reply": replies.identity_bound(),
                    "outcomes": [f"ESCALATE:rider named {named}"]}
        injected = tri.get("injection") or bool(_INJECT_RE.search(text))
        if injected or tri.get("tone_flag") == "threat":
            _record_ops(conn, rider_id, "escalation", None,
                        f"{state['message_id']}: "
                        f"{'injection attempt' if injected else 'threat'}")
            return {"early_reply": replies.escalation("safety"),
                    "outcomes": ["ESCALATE:threat or injection"]}
        if tri.get("asks_human"):
            _record_ops(conn, rider_id, "escalation", None, "rider asked for a human")
            return {"early_reply": replies.escalation("human"),
                    "outcomes": ["ESCALATE:asks_human"]}
        return {}

    def safety_route(state: TurnState) -> str:
        return "respond" if state.get("early_reply") else "claims"

    # ---- node: claims ----
    def claims(state: TurnState) -> TurnState:
        rider_id = state["rider_id"]
        received_at = state["received_at"]
        received_day = datetime.date.fromisoformat(state["received_day"])
        convo = state["convo"]
        tri = state["triage"]
        open_conv_issues = issues.open_issues(conn, rider_id, convo["conversation_id"])

        clms = [c for c in tri["issues"] if c.get("trip_ids") or c.get("days")
                or c.get("claimed_amount")]
        last_issue = _last_issue(conn, rider_id, convo["conversation_id"])
        has_evidence = last_issue is not None and (
            last_issue["ist_day"] is not None or last_issue["trip_ids"])
        if clms and has_evidence and _PUSHBACK_RE.search(text_of(state)) \
                and not any(c.get("trip_ids") for c in clms):
            clms = [{"kind": last_issue["kind"],
                     "trip_ids": list(last_issue["trip_ids"] or []),
                     "days": [str(last_issue["ist_day"])] if last_issue["ist_day"] else [],
                     "claimed_amount": None}]
        if clms:
            clms = [{**EMPTY_CLAIM, **c} for c in clms]
        elif open_conv_issues:
            last = open_conv_issues[-1]
            if last["ist_day"] is not None or last["trip_ids"]:
                clms = [{"kind": last["kind"],
                         "trip_ids": list(last["trip_ids"] or []),
                         "claimed_amount": None,
                         "days": [str(last["ist_day"])] if last["ist_day"] else []}]

        segments: list[str] = []
        outcomes: list[str] = []
        saw_pay = False
        for claim in clms:
            segment, outcome, paid = _handle_claim_verdict(
                conn, rider_id, received_at, received_day, convo, claim, saw_pay)
            saw_pay = saw_pay or paid
            if segment:
                segments.append(segment)
            outcomes.append(outcome)

        if not clms:
            segment, outcome, _ = _no_claim_reply(conn, rider_id, received_day)
            segments.append(segment)
            outcomes.append(outcome)

        reply = " ".join(s for s in segments if s)
        return {"claims": clms, "segments": segments, "outcomes": outcomes,
                "reply": reply}

    def text_of(state: TurnState) -> str:
        return state["text"]

    # ---- node: respond ----
    def respond(state: TurnState) -> TurnState:
        early = state.get("early_reply")
        if early:
            outcomes = state.get("outcomes") or ["ESCALATE"]
            _finish(conn, state["rider_id"], state["message_id"], early,
                    outcomes[0].split(":")[0], outcomes[0])
            return {"reply": early}
        reply = state.get("reply") or ""
        outcomes = state.get("outcomes") or ["NEED_INFO"]
        _finish(conn, state["rider_id"], state["message_id"], reply,
                outcomes[0], f"{len(state.get('claims') or [])} claim(s)")
        return {"reply": reply}

    graph.add_node("triage", triage)
    graph.add_node("safety", safety)
    graph.add_node("claims", claims)
    graph.add_node("respond", respond)
    graph.add_edge(START, "triage")
    graph.add_edge("triage", "safety")
    graph.add_conditional_edges("safety", safety_route,
                                {"claims": "claims", "respond": "respond"})
    graph.add_edge("claims", "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)


def handle_message_graph(conn, graph, rider_id: str, message_id: str, text: str,
                         received_at: str) -> str:
    state = graph.invoke(
        {"rider_id": rider_id, "message_id": message_id, "text": text,
         "received_at": received_at},
        config={"configurable": {"thread_id": f"rider:{rider_id}"}},
    )
    return state["reply"]
