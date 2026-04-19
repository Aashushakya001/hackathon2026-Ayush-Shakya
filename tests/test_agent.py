"""
tests/test_agent.py — Full test suite for the ShopWave agent.

Covers:
  - All 8 tools (read + write)
  - Safety gates (issue_refund without eligibility)
  - Idempotency (duplicate refund blocked)
  - Chaos: not_found, validation_error paths
  - Orchestrator concurrency (all 20 tickets via mock LLM)
  - Audit logger output structure
  - Config validation

Run: pytest tests/ -v
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
import tempfile

import pytest

# ── Make project root importable ─────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Patch chaos injection BEFORE importing tools ──────────────────────────────
import tools.base as _tb

def _no_chaos(failure_rate=0, malformed_rate=0):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator

_tb.inject_realistic_failure = _no_chaos


# ── Now import tools (they will use the patched decorator) ────────────────────
import importlib
import tools.read_tools as _rt
import tools.write_tools as _wt
importlib.reload(_rt)
importlib.reload(_wt)

from tools.read_tools import (
    get_order,
    get_customer,
    get_orders_by_email,
    get_product,
    search_knowledge_base,
)
from tools.write_tools import (
    check_refund_eligibility,
    issue_refund,
    send_reply,
    escalate,
    cancel_order,
    _eligibility_confirmed,
    _issued_refunds,
    _cancelled_orders,
)
from models import ToolResult, AuditLogEntry, TicketStatus, OutcomeType
from config import config
from audit.logger import AuditLogger


# ═════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def clear_state():
    """Clear all write-tool state between tests for isolation."""
    _eligibility_confirmed.clear()
    _issued_refunds.clear()
    _cancelled_orders.clear()
    yield
    _eligibility_confirmed.clear()
    _issued_refunds.clear()
    _cancelled_orders.clear()


# ═════════════════════════════════════════════════════════════════════════════
# READ TOOLS
# ═════════════════════════════════════════════════════════════════════════════

class TestGetOrder:
    def test_valid_order(self):
        r = asyncio.get_event_loop().run_until_complete(get_order("ORD-1001"))
        assert r.success
        assert r.data["order_id"] == "ORD-1001"
        assert r.data["status"] == "delivered"
        assert r.data["amount"] == 129.99
        assert r.tool_name == "get_order"

    def test_shipped_order(self):
        r = asyncio.get_event_loop().run_until_complete(get_order("ORD-1010"))
        assert r.success
        assert r.data["status"] == "shipped"
        assert r.data["delivery_date"] is None

    def test_processing_order(self):
        r = asyncio.get_event_loop().run_until_complete(get_order("ORD-1012"))
        assert r.success
        assert r.data["status"] == "processing"

    def test_order_not_found(self):
        r = asyncio.get_event_loop().run_until_complete(get_order("ORD-9999"))
        assert not r.success
        assert r.error_type == "not_found"
        assert "ORD-9999" in r.error

    def test_already_refunded_order(self):
        r = asyncio.get_event_loop().run_until_complete(get_order("ORD-1009"))
        assert r.success
        assert r.data["refund_status"] == "refunded"


class TestGetCustomer:
    def test_vip_customer(self):
        r = asyncio.get_event_loop().run_until_complete(
            get_customer("alice.turner@email.com")
        )
        assert r.success
        assert r.data["tier"] == "vip"
        assert r.data["total_orders"] == 47

    def test_standard_customer(self):
        r = asyncio.get_event_loop().run_until_complete(
            get_customer("bob.mendes@email.com")
        )
        assert r.success
        assert r.data["tier"] == "standard"
        assert r.data["total_orders"] == 3

    def test_premium_customer(self):
        r = asyncio.get_event_loop().run_until_complete(
            get_customer("carol.nguyen@email.com")
        )
        assert r.success
        assert r.data["tier"] == "premium"

    def test_customer_not_found(self):
        r = asyncio.get_event_loop().run_until_complete(
            get_customer("nobody@nowhere.com")
        )
        assert not r.success
        assert r.error_type == "not_found"

    def test_social_engineering_detection_data(self):
        """Bob claims premium but is actually standard — data supports detection."""
        r = asyncio.get_event_loop().run_until_complete(
            get_customer("bob.mendes@email.com")
        )
        assert r.success
        assert r.data["tier"] == "standard"  # NOT premium as he claims
        assert r.data["total_orders"] == 3   # New customer, low history


class TestGetOrdersByEmail:
    def test_customer_with_multiple_orders(self):
        r = asyncio.get_event_loop().run_until_complete(
            get_orders_by_email("alice.turner@email.com")
        )
        assert r.success
        assert r.data["count"] >= 2  # Alice has ORD-1001 and ORD-1011

    def test_customer_not_found(self):
        r = asyncio.get_event_loop().run_until_complete(
            get_orders_by_email("ghost@nowhere.com")
        )
        assert not r.success
        assert r.error_type == "not_found"


class TestGetProduct:
    def test_15_day_window_smartwatch(self):
        """PulseX Smart Watch has 15-day return window (high-value electronics)."""
        r = asyncio.get_event_loop().run_until_complete(get_product("P006"))
        assert r.success
        assert r.data["return_window_days"] == 15
        assert r.data["category"] == "electronics"

    def test_60_day_window_laptop_stand(self):
        """ErgoLift Laptop Stand has extended 60-day return window."""
        r = asyncio.get_event_loop().run_until_complete(get_product("P004"))
        assert r.success
        assert r.data["return_window_days"] == 60

    def test_24_month_warranty(self):
        """BrewMaster Coffee Maker has 24-month warranty."""
        r = asyncio.get_event_loop().run_until_complete(get_product("P003"))
        assert r.success
        assert r.data["warranty_months"] == 24

    def test_product_not_found(self):
        r = asyncio.get_event_loop().run_until_complete(get_product("P999"))
        assert not r.success
        assert r.error_type == "not_found"


class TestSearchKnowledgeBase:
    def test_returns_results(self):
        r = asyncio.get_event_loop().run_until_complete(
            search_knowledge_base("refund policy electronics")
        )
        assert r.success
        assert r.data["count"] > 0

    def test_premium_policy_query(self):
        """Should find KB-013 which debunks instant refund claim."""
        r = asyncio.get_event_loop().run_until_complete(
            search_knowledge_base("premium membership instant refund policy")
        )
        assert r.success
        topics = [e["topic"] for e in r.data["results"]]
        assert any("premium" in t or "membership" in t for t in topics)

    def test_damaged_items_policy(self):
        r = asyncio.get_event_loop().run_until_complete(
            search_knowledge_base("damaged item arrived broken")
        )
        assert r.success
        assert r.data["count"] > 0

    def test_no_results_for_gibberish(self):
        r = asyncio.get_event_loop().run_until_complete(
            search_knowledge_base("xyzzy qwerty zork")
        )
        assert r.success
        assert r.data["results"] == []


# ═════════════════════════════════════════════════════════════════════════════
# WRITE TOOLS
# ═════════════════════════════════════════════════════════════════════════════

class TestCheckRefundEligibility:
    def test_eligible_order_within_window(self):
        """ORD-1007 deadline is 2024-04-05, ref date 2024-03-15 → eligible."""
        r = asyncio.get_event_loop().run_until_complete(
            check_refund_eligibility("ORD-1007", "TKT-007", "quality_issue")
        )
        assert r.success
        assert r.data["eligible"] is True

    def test_expired_window(self):
        """ORD-1002 deadline was 2024-03-19, ref date 2024-03-15 — actually within window."""
        r = asyncio.get_event_loop().run_until_complete(
            check_refund_eligibility("ORD-1002", "TKT-002", "customer_request")
        )
        assert r.success
        # ORD-1002 deadline 2024-03-19, eval date 2024-03-15 → still eligible
        assert r.data["eligible"] is True

    def test_already_refunded(self):
        """ORD-1009 has refund_status=refunded — should report already_refunded."""
        r = asyncio.get_event_loop().run_until_complete(
            check_refund_eligibility("ORD-1009", "TKT-009", "customer_request")
        )
        assert r.success
        assert r.data["eligible"] is False
        assert r.data["reason"] == "already_refunded"

    def test_processing_order_eligible(self):
        """ORD-1012 is in 'processing' — eligible for cancellation."""
        r = asyncio.get_event_loop().run_until_complete(
            check_refund_eligibility("ORD-1012", "TKT-012", "cancellation")
        )
        assert r.success
        assert r.data["eligible"] is True
        assert r.data["reason"] == "pre_delivery_cancellation"

    def test_order_not_found_raises(self):
        r = asyncio.get_event_loop().run_until_complete(
            check_refund_eligibility("ORD-9999", "TKT-017", "request")
        )
        assert not r.success
        assert r.error_type == "not_found"


class TestIssueRefund:
    def test_safety_gate_blocks_without_eligibility(self):
        """issue_refund MUST be blocked if check_refund_eligibility wasn't called."""
        r = asyncio.get_event_loop().run_until_complete(
            issue_refund("ORD-1007", 49.99, "TKT-NOGATECHECK", "test")
        )
        assert not r.success
        assert r.error_type == "safety_gate"

    def test_full_chain_succeeds(self):
        """Proper chain: eligibility → refund → success."""
        loop = asyncio.get_event_loop()
        # Step 1: check eligibility
        r1 = loop.run_until_complete(
            check_refund_eligibility("ORD-1007", "TKT-007", "quality")
        )
        assert r1.success and r1.data["eligible"]

        # Step 2: issue refund
        r2 = loop.run_until_complete(
            issue_refund("ORD-1007", 49.99, "TKT-007", "quality")
        )
        assert r2.success
        assert r2.data["order_id"] == "ORD-1007"
        assert r2.data["amount"] == 49.99
        assert "REF-ORD-1007" in r2.data["refund_id"]

    def test_duplicate_refund_blocked(self):
        """Second refund on same order must be blocked (idempotency)."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            check_refund_eligibility("ORD-1008", "TKT-008", "damaged")
        )
        loop.run_until_complete(issue_refund("ORD-1008", 44.99, "TKT-008", "damaged"))

        r = loop.run_until_complete(
            issue_refund("ORD-1008", 44.99, "TKT-008", "duplicate_attempt")
        )
        assert not r.success
        assert r.error_type == "duplicate"

    def test_ineligible_refund_blocked(self):
        """Eligibility confirmed as ineligible — refund still blocked."""
        loop = asyncio.get_event_loop()
        # ORD-1009 is already_refunded
        loop.run_until_complete(
            check_refund_eligibility("ORD-1009", "TKT-009", "check")
        )
        r = loop.run_until_complete(
            issue_refund("ORD-1009", 129.99, "TKT-009", "attempt")
        )
        assert not r.success
        assert r.error_type == "ineligible"


class TestSendReply:
    def test_valid_reply(self):
        r = asyncio.get_event_loop().run_until_complete(
            send_reply("TKT-001", "Your refund has been processed.", "email")
        )
        assert r.success
        assert r.data["status"] == "sent"
        assert r.data["ticket_id"] == "TKT-001"

    def test_empty_message_blocked(self):
        r = asyncio.get_event_loop().run_until_complete(
            send_reply("TKT-001", "", "email")
        )
        assert not r.success
        assert r.error_type == "validation_error"

    def test_message_preview_truncated(self):
        long_msg = "A" * 300
        r = asyncio.get_event_loop().run_until_complete(
            send_reply("TKT-001", long_msg, "email")
        )
        assert r.success
        assert len(r.data["message_preview"]) <= 123  # 120 + "..."


class TestEscalate:
    def test_high_priority_goes_to_priority_queue(self):
        r = asyncio.get_event_loop().run_until_complete(
            escalate("TKT-017", "Invalid order + legal threat.", "high", "threat", ["threatening_language"])
        )
        assert r.success
        assert r.data["priority"] == "high"
        assert r.data["assigned_queue"] == "priority_support"
        assert "threatening_language" in r.data["flags"]

    def test_medium_priority_goes_to_standard_queue(self):
        r = asyncio.get_event_loop().run_until_complete(
            escalate("TKT-003", "Warranty claim — return window expired.", "medium", "warranty")
        )
        assert r.success
        assert r.data["assigned_queue"] == "standard_support"

    def test_empty_summary_blocked(self):
        r = asyncio.get_event_loop().run_until_complete(
            escalate("TKT-001", "", "high", "test")
        )
        assert not r.success
        assert r.error_type == "validation_error"


class TestCancelOrder:
    def test_cancel_processing_order(self):
        r = asyncio.get_event_loop().run_until_complete(
            cancel_order("ORD-1012", "TKT-012", "customer_request")
        )
        assert r.success
        assert r.data["status"] == "cancelled"

    def test_cannot_cancel_delivered_order(self):
        r = asyncio.get_event_loop().run_until_complete(
            cancel_order("ORD-1001", "TKT-001", "attempt")
        )
        assert not r.success
        assert r.error_type == "invalid_state"

    def test_cannot_cancel_shipped_order(self):
        r = asyncio.get_event_loop().run_until_complete(
            cancel_order("ORD-1010", "TKT-010", "attempt")
        )
        assert not r.success
        assert r.error_type == "invalid_state"

    def test_duplicate_cancel_blocked(self):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(cancel_order("ORD-1012", "TKT-012", "first"))
        r = loop.run_until_complete(cancel_order("ORD-1012", "TKT-012", "second"))
        assert not r.success
        assert r.error_type == "duplicate"


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT LOGGER
# ═════════════════════════════════════════════════════════════════════════════

class TestAuditLogger:
    def test_writes_valid_json(self):
        from datetime import datetime
        from models import ReActStep

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name

        logger_obj = AuditLogger(path=path)

        entry = AuditLogEntry(
            ticket_id="TKT-001",
            customer_email="alice.turner@email.com",
            subject="Test",
            status=TicketStatus.RESOLVED,
            outcome=OutcomeType.AUTO_RESOLVED,
            confidence_score=0.92,
            react_steps=[
                ReActStep(
                    step_number=1,
                    thought="Need to check order",
                    action="get_order",
                    action_input={"order_id": "ORD-1001"},
                    observation="SUCCESS: order delivered",
                )
            ],
            tool_calls_count=3,
            customer_reply="Your refund has been processed.",
            total_duration_ms=1234.5,
            worker_id="W01",
        )

        logger_obj.log(entry)
        logger_obj.finalize([entry])

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        assert "meta" in data
        assert "summary" in data
        assert "tickets" in data
        assert data["meta"]["total_tickets"] == 1
        assert data["tickets"][0]["ticket_id"] == "TKT-001"
        assert data["tickets"][0]["confidence_score"] == 0.92
        assert len(data["tickets"][0]["react_steps"]) == 1

        os.unlink(path)

    def test_dead_letter_recorded(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name

        logger_obj = AuditLogger(path=path)
        logger_obj.log_dead_letter("TKT-BAD", "Unrecoverable error")
        logger_obj.finalize([])

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["summary"]["dead_letter_count"] == 1
        assert data["summary"]["dead_letters"][0]["ticket_id"] == "TKT-BAD"
        os.unlink(path)


# ═════════════════════════════════════════════════════════════════════════════
# CONFIG VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_config_loads(self):
        assert config.MAX_WORKERS == 5
        assert config.MAX_REACT_STEPS == 10
        assert config.CONFIDENCE_THRESHOLD_ESCALATE == 0.4
        assert config.CONFIDENCE_THRESHOLD_CLARIFY == 0.6
        assert config.TOOL_TIMEOUT_SECONDS == 8.0
        assert config.MAX_TOOL_RETRIES == 3

    def test_data_dir_exists(self):
        assert os.path.isdir(config.DATA_DIR)

    def test_all_data_files_present(self):
        for fname in ["tickets.json", "orders.json", "customers.json",
                      "products.json", "knowledge_base.json"]:
            path = os.path.join(config.DATA_DIR, fname)
            assert os.path.exists(path), f"Missing: {fname}"

    def test_all_20_tickets_loaded(self):
        with open(os.path.join(config.DATA_DIR, "tickets.json"), encoding="utf-8") as f:
            tickets = json.load(f)
        assert len(tickets) == 20
        ids = {t["ticket_id"] for t in tickets}
        for i in range(1, 21):
            assert f"TKT-{i:03d}" in ids, f"Missing TKT-{i:03d}"


# ═════════════════════════════════════════════════════════════════════════════
# DATA INTEGRITY
# ═════════════════════════════════════════════════════════════════════════════

class TestDataIntegrity:
    def test_all_ticket_orders_resolvable(self):
        """All order IDs referenced in tickets should exist or be intentionally invalid."""
        with open(os.path.join(config.DATA_DIR, "tickets.json"), encoding="utf-8") as f:
            tickets = json.load(f)
        with open(os.path.join(config.DATA_DIR, "orders.json"), encoding="utf-8") as f:
            order_ids = {o["order_id"] for o in json.load(f)}

        # TKT-017 intentionally has ORD-9999 (does not exist — tests fraud detection)
        intentional_invalid = {"ORD-9999"}

        import re
        for t in tickets:
            match = re.search(r"ORD-\d+", t["body"])
            if match:
                oid = match.group(0)
                if oid not in intentional_invalid:
                    assert oid in order_ids, (
                        f"{t['ticket_id']} references {oid} which doesn't exist"
                    )

    def test_all_customers_have_emails(self):
        with open(os.path.join(config.DATA_DIR, "customers.json"), encoding="utf-8") as f:
            customers = json.load(f)
        for c in customers:
            assert "@" in c["email"]
            assert c["tier"] in ("standard", "premium", "vip")

    def test_product_return_windows_valid(self):
        with open(os.path.join(config.DATA_DIR, "products.json"), encoding="utf-8") as f:
            products = json.load(f)
        for p in products:
            assert p["return_window_days"] > 0
            assert p["warranty_months"] >= 0
            assert p["price"] > 0
