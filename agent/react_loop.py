"""
agent/react_loop.py — LangGraph-based ReAct reasoning loop.

Architecture:
  START → classify → react_step (loop) → finalize → END

Each node in the graph corresponds to one stage of reasoning.
The react_step node loops until done=True or max_steps reached.
Tool calls happen inside react_step using the tool registry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict, Annotated

from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages

from models import (
    AuditLogEntry,
    ReActStep,
    ToolResult,
    TicketStatus,
    OutcomeType,
    Priority,
    TriageResult,
)
from agent.llm_client import llm_call
from agent.prompts import (
    TRIAGE_SYSTEM_PROMPT,
    REACT_SYSTEM_PROMPT,
    build_react_user_prompt,
    build_react_continuation_prompt,
)
from tools import (
    get_order,
    get_customer,
    get_orders_by_email,
    get_product,
    search_knowledge_base,
    check_refund_eligibility,
    issue_refund,
    send_reply,
    escalate,
    cancel_order,
)
from config import config

logger = logging.getLogger(__name__)

# ─── Tool Registry ────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, Any] = {
    "get_order": get_order,
    "get_customer": get_customer,
    "get_orders_by_email": get_orders_by_email,
    "get_product": get_product,
    "search_knowledge_base": search_knowledge_base,
    "check_refund_eligibility": check_refund_eligibility,
    "issue_refund": issue_refund,
    "send_reply": send_reply,
    "escalate": escalate,
    "cancel_order": cancel_order,
}

# ─── Graph State ──────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    ticket: dict
    triage: Optional[dict]
    steps: list[dict]          # ReAct steps accumulated
    step_count: int
    done: bool
    final_outcome: Optional[str]
    customer_reply: Optional[str]
    escalation_summary: Optional[str]
    confidence: float
    flags: list[str]
    error: Optional[str]
    worker_id: str

# ─── Node: Classify & Triage ─────────────────────────────────────────────────

async def classify_node(state: AgentState) -> AgentState:
    """Classifies the ticket: category, urgency, resolvability, confidence."""
    ticket = state["ticket"]
    logger.info(f"[{state['worker_id']}] Classifying {ticket['ticket_id']}")

    user_prompt = (
        f"Ticket ID: {ticket['ticket_id']}\n"
        f"Subject: {ticket['subject']}\n"
        f"Body: {ticket['body']}\n"
        f"Customer Email: {ticket['customer_email']}\n"
        f"Source: {ticket['source']}"
    )

    try:
        triage_data = await llm_call(
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            expect_json=True,
            temperature=0.0,
        )
        triage = TriageResult(**triage_data)
        logger.info(
            f"[{state['worker_id']}] {ticket['ticket_id']} → "
            f"category={triage.category}, urgency={triage.urgency}, "
            f"confidence={triage.confidence:.2f}"
        )
        return {**state, "triage": triage.model_dump()}
    except Exception as e:
        logger.error(f"[{state['worker_id']}] Triage failed: {e}")
        return {
            **state,
            "triage": {
                "category": "ambiguous",
                "urgency": "medium",
                "resolvability": "clarify",
                "confidence": 0.3,
                "order_id_extracted": None,
                "flags": [],
                "reasoning": f"Triage failed: {str(e)[:100]}",
            },
        }

# ─── Node: ReAct Step ─────────────────────────────────────────────────────────

async def react_step_node(state: AgentState) -> AgentState:
    """
    One iteration of the ReAct loop:
      1. LLM produces Thought + Action
      2. Tool is executed
      3. Observation is recorded
      4. Loop continues until done=True or max_steps
    """
    ticket = state["ticket"]
    steps = state["steps"]
    step_num = state["step_count"] + 1
    worker = state["worker_id"]

    logger.info(
        f"[{worker}] {ticket['ticket_id']} — ReAct step {step_num}/{config.MAX_REACT_STEPS}"
    )

    # Build step history string for context
    step_history = _format_step_history(steps)
    last_obs = steps[-1]["observation"] if steps else ""

    if not steps:
        user_prompt = build_react_user_prompt(ticket, state["triage"])
    else:
        user_prompt = build_react_continuation_prompt(
            ticket, step_history, last_obs, step_num, config.MAX_REACT_STEPS
        )

    try:
        llm_response = await llm_call(
            system_prompt=REACT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            expect_json=True,
            temperature=0.1,
            max_tokens=1200,
        )
    except Exception as e:
        logger.error(f"[{worker}] LLM call failed at step {step_num}: {e}")
        return {
            **state,
            "done": True,
            "error": f"LLM failure at step {step_num}: {str(e)}",
            "step_count": step_num,
        }

    thought = llm_response.get("thought", "")
    action = llm_response.get("action", "FINISH")
    action_input = llm_response.get("action_input", {})
    is_done = llm_response.get("done", False)
    confidence = llm_response.get("confidence", 0.5)

    # ── Execute tool ──────────────────────────────────────────────────────────
    observation = ""
    tool_result: Optional[ToolResult] = None

    if action != "FINISH" and action in TOOL_REGISTRY:
        tool_fn = TOOL_REGISTRY[action]
        logger.info(f"[{worker}] {ticket['ticket_id']} → calling {action}({action_input})")

        # Inject ticket_id into write tool calls automatically
        if action in ("check_refund_eligibility", "issue_refund", "cancel_order", "send_reply", "escalate"):
            if "ticket_id" not in action_input:
                action_input["ticket_id"] = ticket["ticket_id"]

        try:
            tool_result = await tool_fn(**action_input)
            if tool_result.success:
                observation = f"SUCCESS: {json.dumps(tool_result.data, default=str)[:800]}"
            else:
                observation = (
                    f"TOOL FAILED [{tool_result.error_type}]: {tool_result.error}. "
                    f"Retries used: {tool_result.retries_used}. "
                    "Consider alternative approach or escalate."
                )
        except TypeError as e:
            observation = f"TOOL CALL ERROR: Invalid arguments for {action}: {e}"
            tool_result = ToolResult(
                tool_name=action,
                success=False,
                error=str(e),
                error_type="invalid_args",
            )
    elif action == "FINISH" or is_done:
        observation = "Agent has concluded processing."
    else:
        observation = f"UNKNOWN TOOL: '{action}'. Available: {list(TOOL_REGISTRY.keys())}"
        logger.warning(f"[{worker}] Unknown tool: {action}")

    # ── Record step ───────────────────────────────────────────────────────────
    step_record = {
        "step_number": step_num,
        "thought": thought,
        "action": action,
        "action_input": action_input,
        "observation": observation[:1000],
        "tool_result": tool_result.model_dump() if tool_result else None,
        "confidence": confidence,
    }
    new_steps = steps + [step_record]

    # ── Check termination ─────────────────────────────────────────────────────
    done = is_done or action == "FINISH" or step_num >= config.MAX_REACT_STEPS

    # Extract outcome from final step
    final_outcome = None
    customer_reply = None
    escalation_summary = None

    if done:
        final_outcome = llm_response.get("outcome")
        customer_reply = llm_response.get("customer_reply")
        escalation_summary = llm_response.get("escalation_summary")
        logger.info(
            f"[{worker}] {ticket['ticket_id']} → DONE. "
            f"outcome={final_outcome}, steps={step_num}"
        )

    flags = state.get("flags", []) + state["triage"].get("flags", [])

    return {
        **state,
        "steps": new_steps,
        "step_count": step_num,
        "done": done,
        "confidence": confidence,
        "flags": list(set(flags)),
        "final_outcome": final_outcome or state.get("final_outcome"),
        "customer_reply": customer_reply or state.get("customer_reply"),
        "escalation_summary": escalation_summary or state.get("escalation_summary"),
    }

# ─── Routing: should we loop or finish? ──────────────────────────────────────

def should_continue(state: AgentState) -> str:
    if state.get("done") or state.get("error"):
        return "finalize"
    if state["step_count"] >= config.MAX_REACT_STEPS:
        return "finalize"
    return "react_step"

# ─── Node: Finalize ───────────────────────────────────────────────────────────

async def finalize_node(state: AgentState) -> AgentState:
    """
    If the agent didn't explicitly finish with send_reply/escalate,
    this node ensures a safe fallback (escalate) so no ticket is abandoned.
    """
    ticket = state["ticket"]
    worker = state["worker_id"]

    if not state.get("final_outcome"):
        logger.warning(
            f"[{worker}] {ticket['ticket_id']} — no final outcome set. "
            "Triggering safety escalation."
        )
        # Force escalation as safety net
        await escalate(
            ticket_id=ticket["ticket_id"],
            summary=(
                f"Agent reached max steps ({config.MAX_REACT_STEPS}) without "
                f"resolving ticket. Steps taken: {state['step_count']}. "
                f"Triage: {state['triage']}. Manual review required."
            ),
            priority="medium",
            reason="max_steps_reached",
        )
        return {
            **state,
            "final_outcome": "escalated",
            "escalation_summary": "Max steps reached — escalated for human review.",
        }

    logger.info(
        f"[{worker}] {ticket['ticket_id']} finalized. "
        f"outcome={state['final_outcome']}"
    )
    return state

# ─── Build the LangGraph ──────────────────────────────────────────────────────

def build_agent_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("classify", classify_node)
    graph.add_node("react_step", react_step_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "classify")
    graph.add_edge("classify", "react_step")
    graph.add_conditional_edges(
        "react_step",
        should_continue,
        {"react_step": "react_step", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)

    return graph.compile()

# ─── Public API ───────────────────────────────────────────────────────────────

AGENT_GRAPH = build_agent_graph()


async def process_ticket(ticket: dict, worker_id: str) -> AuditLogEntry:
    """
    Main entry point: processes a single ticket through the full LangGraph.
    Returns a complete AuditLogEntry with all steps, outcome, and metadata.
    """
    start_time = time.monotonic()
    ticket_id = ticket["ticket_id"]

    logger.info(f"[{worker_id}] START processing {ticket_id}")

    initial_state: AgentState = {
        "ticket": ticket,
        "triage": None,
        "steps": [],
        "step_count": 0,
        "done": False,
        "final_outcome": None,
        "customer_reply": None,
        "escalation_summary": None,
        "confidence": 0.5,
        "flags": [],
        "error": None,
        "worker_id": worker_id,
    }

    try:
        final_state = await AGENT_GRAPH.ainvoke(initial_state)
    except Exception as e:
        logger.error(f"[{worker_id}] Graph failed for {ticket_id}: {e}")
        final_state = {**initial_state, "error": str(e), "done": True}

    duration_ms = (time.monotonic() - start_time) * 1000

    # Build audit log entry
    react_steps = [
        ReActStep(
            step_number=s["step_number"],
            thought=s["thought"],
            action=s["action"],
            action_input=s["action_input"],
            observation=s["observation"],
            tool_result=(
                ToolResult(**s["tool_result"]) if s.get("tool_result") else None
            ),
        )
        for s in final_state.get("steps", [])
    ]

    status = TicketStatus.FAILED if final_state.get("error") else TicketStatus.RESOLVED
    if final_state.get("final_outcome") == "escalated":
        status = TicketStatus.ESCALATED

    outcome_map = {
        "auto_resolved": OutcomeType.AUTO_RESOLVED,
        "informational": OutcomeType.INFORMATIONAL,
        "escalated": OutcomeType.ESCALATED,
        "flagged": OutcomeType.FLAGGED,
        "clarification_requested": OutcomeType.CLARIFICATION_REQUESTED,
        "cancelled": OutcomeType.CANCELLED,
    }

    outcome = outcome_map.get(
        final_state.get("final_outcome", ""), OutcomeType.ESCALATED
    )

    entry = AuditLogEntry(
        ticket_id=ticket_id,
        customer_email=ticket["customer_email"],
        subject=ticket["subject"],
        status=status,
        outcome=outcome,
        confidence_score=final_state.get("confidence", 0.0),
        react_steps=react_steps,
        tool_calls_count=len([
            s for s in final_state.get("steps", [])
            if s["action"] not in ("FINISH",)
        ]),
        customer_reply=final_state.get("customer_reply"),
        escalation_summary=final_state.get("escalation_summary"),
        flags=list(set(final_state.get("flags", []))),
        error=final_state.get("error"),
        processing_completed_at=datetime.now(timezone.utc).isoformat(),
        total_duration_ms=duration_ms,
        worker_id=worker_id,
    )

    logger.info(
        f"[{worker_id}] DONE {ticket_id} | outcome={outcome.value} | "
        f"steps={len(react_steps)} | tool_calls={entry.tool_calls_count} | "
        f"{duration_ms:.0f}ms"
    )

    return entry

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _format_step_history(steps: list[dict]) -> str:
    lines = []
    for s in steps:
        lines.append(f"Step {s['step_number']}:")
        lines.append(f"  Thought: {s['thought']}")
        lines.append(f"  Action: {s['action']}({json.dumps(s['action_input'])})")
        lines.append(f"  Observation: {s['observation'][:300]}")
    return "\n".join(lines)
