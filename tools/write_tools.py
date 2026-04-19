"""
tools/write_tools.py — All WRITE/ACT tools (irreversible actions).

CRITICAL SAFETY RULE:
  issue_refund() CANNOT be called without a prior successful
  check_refund_eligibility() call. This is enforced at the tool level
  via a per-ticket guard set. The ReAct loop also enforces this in prompt.

Every action is logged to the audit trail.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from models import ToolResult, Priority
from tools.base import tool_call, inject_realistic_failure
from config import config

logger = logging.getLogger(__name__)

# Per-ticket eligibility guard (ticket_id → order_id)
# issue_refund checks this before executing
_eligibility_confirmed: dict[str, str] = {}

# Track refunds already issued (idempotency)
_issued_refunds: set[str] = set()

# Track cancelled orders
_cancelled_orders: set[str] = set()

# Load orders for status checks
def _load_orders() -> dict:
    path = os.path.join(config.DATA_DIR, "orders.json")
    with open(path, encoding="utf-8") as f:
        return {o["order_id"]: o for o in json.load(f)}

_ORDERS = _load_orders()

# ─── Tool: check_refund_eligibility ──────────────────────────────────────────

@tool_call("check_refund_eligibility", timeout=8.0)
@inject_realistic_failure(failure_rate=0.15, malformed_rate=0.10)
async def check_refund_eligibility(
    order_id: str,
    ticket_id: str,
    reason: str = "customer_request",
) -> ToolResult:
    """
    Checks if an order is eligible for refund.
    Returns eligibility + detailed reason.
    MUST be called before issue_refund — sets the eligibility guard.
    May throw errors (as per hackathon spec).
    """
    await asyncio.sleep(0.06)

    if order_id not in _ORDERS:
        raise KeyError(f"Order '{order_id}' not found — cannot check eligibility")

    order = _ORDERS[order_id]

    # Already refunded?
    if order.get("refund_status") == "refunded" or order_id in _issued_refunds:
        result = {
            "eligible": False,
            "reason": "already_refunded",
            "message": f"Order {order_id} has already been refunded.",
            "amount": order["amount"],
        }
        logger.info(f"[check_refund_eligibility] {order_id} → already refunded")
        # Still set guard so agent can report status
        _eligibility_confirmed[f"{ticket_id}:{order_id}"] = "already_refunded"
        return ToolResult(tool_name="check_refund_eligibility", success=True, data=result)

    # Order not delivered yet?
    if order["status"] in ("processing", "shipped"):
        result = {
            "eligible": True,
            "reason": "pre_delivery_cancellation",
            "message": f"Order {order_id} has not been delivered. Eligible for cancellation/refund.",
            "amount": order["amount"],
        }
        _eligibility_confirmed[f"{ticket_id}:{order_id}"] = "eligible"
        return ToolResult(tool_name="check_refund_eligibility", success=True, data=result)

    # Check return window
    return_deadline = order.get("return_deadline")
    if return_deadline:
        deadline_dt = datetime.strptime(return_deadline, "%Y-%m-%d")
        # Use ticket evaluation date as reference
        # Tickets were created around 2024-03-15; use that as evaluation point
        eval_date = datetime(2024, 3, 15)  # reference date from ticket data
        if eval_date > deadline_dt:
            result = {
                "eligible": False,
                "reason": "return_window_expired",
                "message": (
                    f"Order {order_id} return window expired on {return_deadline}. "
                    "Standard refund not available. Check warranty if applicable."
                ),
                "amount": order["amount"],
                "deadline_was": return_deadline,
            }
            logger.info(f"[check_refund_eligibility] {order_id} → window expired")
            _eligibility_confirmed[f"{ticket_id}:{order_id}"] = "ineligible_expired"
            return ToolResult(tool_name="check_refund_eligibility", success=True, data=result)

    # Eligible
    result = {
        "eligible": True,
        "reason": reason,
        "message": f"Order {order_id} is eligible for refund. Amount: ${order['amount']:.2f}",
        "amount": order["amount"],
        "return_deadline": return_deadline,
    }
    _eligibility_confirmed[f"{ticket_id}:{order_id}"] = "eligible"
    logger.info(f"[check_refund_eligibility] {order_id} → ELIGIBLE ${order['amount']:.2f}")
    return ToolResult(tool_name="check_refund_eligibility", success=True, data=result)


# ─── Tool: issue_refund ───────────────────────────────────────────────────────

@tool_call("issue_refund", timeout=10.0)
async def issue_refund(
    order_id: str,
    amount: float,
    ticket_id: str,
    reason: str = "approved",
) -> ToolResult:
    """
    IRREVERSIBLE — Issues a refund for an order.

    SAFETY GATE: Will refuse to execute if check_refund_eligibility()
    was not successfully called for this ticket+order combination.
    This prevents accidental refunds on ineligible orders.
    """
    await asyncio.sleep(0.08)

    guard_key = f"{ticket_id}:{order_id}"

    # ── SAFETY GATE ──────────────────────────────────────────────────
    if guard_key not in _eligibility_confirmed:
        logger.error(
            f"[issue_refund] BLOCKED — eligibility not confirmed for {guard_key}. "
            "check_refund_eligibility() must be called first."
        )
        return ToolResult(
            tool_name="issue_refund",
            success=False,
            error=(
                "SAFETY VIOLATION: issue_refund called without prior "
                "check_refund_eligibility confirmation. Refund blocked."
            ),
            error_type="safety_gate",
        )

    eligibility_status = _eligibility_confirmed[guard_key]
    if eligibility_status in ("ineligible_expired", "already_refunded"):
        return ToolResult(
            tool_name="issue_refund",
            success=False,
            error=f"Refund blocked: eligibility_status={eligibility_status}",
            error_type="ineligible",
        )

    # Idempotency check
    if order_id in _issued_refunds:
        return ToolResult(
            tool_name="issue_refund",
            success=False,
            error=f"Refund for {order_id} already issued in this session.",
            error_type="duplicate",
        )

    # Issue refund
    _issued_refunds.add(order_id)
    refund_id = f"REF-{order_id}-{int(datetime.now(timezone.utc).timestamp())}"

    result = {
        "refund_id": refund_id,
        "order_id": order_id,
        "amount": amount,
        "reason": reason,
        "status": "processed",
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "expected_bank_days": "5-7 business days",
    }

    logger.info(
        f"[issue_refund] ISSUED {refund_id} for {order_id} "
        f"amount=${amount:.2f}"
    )
    return ToolResult(tool_name="issue_refund", success=True, data=result)


# ─── Tool: send_reply ─────────────────────────────────────────────────────────

@tool_call("send_reply", timeout=6.0)
async def send_reply(
    ticket_id: str,
    message: str,
    channel: str = "email",
) -> ToolResult:
    """
    Sends a response to the customer.
    In production this would integrate with an email/ticketing API.
    Here it logs the message and confirms send.
    """
    await asyncio.sleep(0.02)

    if not message.strip():
        raise ValueError("Message cannot be empty")
    if len(message) > 5000:
        raise ValueError("Message exceeds maximum length of 5000 characters")

    result = {
        "ticket_id": ticket_id,
        "channel": channel,
        "status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "message_preview": message[:120] + ("..." if len(message) > 120 else ""),
    }

    logger.info(f"[send_reply] {ticket_id} → reply sent via {channel}")
    return ToolResult(tool_name="send_reply", success=True, data=result)


# ─── Tool: escalate ───────────────────────────────────────────────────────────

@tool_call("escalate", timeout=6.0)
async def escalate(
    ticket_id: str,
    summary: str,
    priority: str = "medium",
    reason: str = "requires_human_review",
    flags: Optional[list[str]] = None,
) -> ToolResult:
    """
    Routes ticket to a human agent with full structured context.
    Used when agent cannot auto-resolve or detects risk/fraud.
    """
    await asyncio.sleep(0.02)

    if not summary.strip():
        raise ValueError("Escalation summary cannot be empty")

    valid_priorities = {p.value for p in Priority}
    if priority not in valid_priorities:
        priority = "medium"

    result = {
        "ticket_id": ticket_id,
        "escalated_at": datetime.now(timezone.utc).isoformat(),
        "priority": priority,
        "reason": reason,
        "summary": summary,
        "flags": flags or [],
        "assigned_queue": (
            "priority_support" if priority in ("high", "critical")
            else "standard_support"
        ),
        "status": "escalated",
    }

    logger.info(
        f"[escalate] {ticket_id} → priority={priority}, reason={reason}"
    )
    return ToolResult(tool_name="escalate", success=True, data=result)


# ─── Tool: cancel_order ───────────────────────────────────────────────────────

@tool_call("cancel_order", timeout=6.0)
async def cancel_order(
    order_id: str,
    ticket_id: str,
    reason: str = "customer_request",
) -> ToolResult:
    """
    Cancels an order that is still in 'processing' status.
    Cannot cancel shipped or delivered orders.
    """
    await asyncio.sleep(0.04)

    if order_id not in _ORDERS:
        raise KeyError(f"Order '{order_id}' not found")

    order = _ORDERS[order_id]

    if order["status"] not in ("processing",):
        return ToolResult(
            tool_name="cancel_order",
            success=False,
            error=(
                f"Cannot cancel order {order_id} — "
                f"status is '{order['status']}'. "
                "Only 'processing' orders can be cancelled."
            ),
            error_type="invalid_state",
        )

    if order_id in _cancelled_orders:
        return ToolResult(
            tool_name="cancel_order",
            success=False,
            error=f"Order {order_id} already cancelled.",
            error_type="duplicate",
        )

    _cancelled_orders.add(order_id)
    result = {
        "order_id": order_id,
        "ticket_id": ticket_id,
        "status": "cancelled",
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "refund_note": "Full refund will be issued automatically within 5-7 business days.",
    }

    logger.info(f"[cancel_order] {order_id} → CANCELLED")
    return ToolResult(tool_name="cancel_order", success=True, data=result)
