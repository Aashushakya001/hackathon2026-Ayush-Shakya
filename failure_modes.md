# Failure Mode Analysis — ShopWave Support Agent

> **Required deliverable:** At least 3 documented failure scenarios with system response.

---

## Failure Mode 1 — Tool Timeout (check_refund_eligibility)

**What happens:**  
`check_refund_eligibility` hangs and exceeds the 8-second timeout (15% probability via chaos injection). This is the most dangerous tool because `issue_refund` cannot be called without it.

**Trigger:**  
Simulated via `asyncio.sleep(10)` inside the `@inject_realistic_failure` decorator, caught by `asyncio.wait_for(timeout=8.0)` in `@tool_call`.

**System response:**
1. `asyncio.TimeoutError` is caught inside the `@tool_call` decorator
2. Error is logged: `[check_refund_eligibility] timeout on attempt 1`
3. Retry 1: exponential backoff waits 1.0s, retries tool call
4. Retry 2: backoff waits 2.1s (1s × 2¹ + jitter), retries again
5. After 3 failed attempts: returns `ToolResult(success=False, error_type="timeout")`
6. ReAct loop receives `TOOL FAILED [timeout]` as observation
7. LLM reasons: "eligibility check unavailable — cannot issue refund safely"
8. Agent calls `escalate(ticket_id, summary="Eligibility check timed out after 3 retries. Manual review required.", priority="medium")`
9. Ticket goes to human queue. **System does not crash. Other workers continue unaffected.**

**Code location:** `tools/base.py` → `tool_call` decorator, `asyncio.wait_for`

---

## Failure Mode 2 — Malformed Tool Response (get_order)

**What happens:**  
`get_order` returns a response where a required field is `null` or structurally invalid (8% probability). Pydantic validation fails before the data is used.

**Trigger:**  
Simulated via `raise ValueError("Malformed response: unexpected null in required field 'status'")` inside `@inject_realistic_failure`.

**System response:**
1. `ValueError` is caught inside `@tool_call` — treated as **non-retriable** (bad data won't fix itself on retry)
2. Returns immediately: `ToolResult(success=False, error_type="validation_error")`
3. No retry attempted (fast-fail on data corruption)
4. ReAct loop sees: `TOOL FAILED [validation_error]: Malformed response...`
5. LLM reasons: "Cannot trust order data — will attempt to proceed with available customer data and ask for clarification"
6. Agent calls `send_reply` asking customer to confirm order details
7. Ticket logged with `error_type: validation_error` in audit log
8. **Other tickets in parallel workers are unaffected.**

**Code location:** `tools/base.py` → `ValueError` branch in `tool_call` decorator (no retry)

---

## Failure Mode 3 — Social Engineering / Invalid Order ID

**What happens:**  
Customer (TKT-017, TKT-018) provides a non-existent order ID (`ORD-9999`) or falsely claims a premium tier/policy. Agent must detect and decline without being manipulated.

**Trigger:**  
`get_order("ORD-9999")` raises `KeyError` (order not in data store). Separately, `get_customer` returns `tier="standard"` contradicting the customer's claim of "premium instant refund."

**System response for invalid order:**
1. `get_order("ORD-9999")` raises `KeyError`
2. Caught by `@tool_call` → `ToolResult(success=False, error_type="not_found")`
3. No retry (not-found won't resolve itself)
4. LLM sees: `TOOL FAILED [not_found]: Order 'ORD-9999' not found`
5. Combined with threatening language flag → urgency escalated to HIGH
6. Agent calls `escalate(priority="high", flags=["threatening_language", "invalid_order_id"])`
7. Professional reply sent: no apologies, no policy bypass

**System response for false tier claim (TKT-018):**
1. Agent calls `get_customer("bob.mendes@email.com")` → returns `tier="standard"`, `total_orders=3`
2. Agent calls `search_knowledge_base("premium instant refund policy")` → returns KB-013: no such policy exists
3. Agent calls `check_refund_eligibility("ORD-1002", ...)` → return window expired
4. LLM identifies social engineering: tier claim is false, policy does not exist, window expired
5. Flags ticket: `["social_engineering", "false_tier_claim"]`
6. Sends polite but firm decline. Escalates with HIGH priority for fraud monitoring.
7. `issue_refund` is **never called** — all three gate checks fail.

**Code location:** `tools/base.py` → `KeyError` branch; `tools/write_tools.py` → eligibility guard

---

## Failure Mode 4 — Agent Reaches Max Steps Without Resolution

**What happens:**  
An extremely ambiguous ticket causes the ReAct loop to exhaust `MAX_REACT_STEPS` (10) without reaching a conclusion.

**Trigger:**  
Edge case where LLM keeps requesting more information or tool calls loop without converging.

**System response:**
1. `should_continue()` routing function detects `step_count >= MAX_REACT_STEPS`
2. Routes to `finalize_node` instead of another `react_step`
3. `finalize_node` detects `final_outcome is None`
4. Automatically calls `escalate(reason="max_steps_reached", priority="medium")`
5. Full step history preserved in audit log for human review
6. **No ticket is ever silently dropped.** Either resolved, escalated, or dead-lettered.

**Code location:** `agent/react_loop.py` → `should_continue()`, `finalize_node()`

---

## Failure Mode 5 — Worker Crash / Unhandled Exception

**What happens:**  
An unexpected exception (memory error, network failure, etc.) causes a worker coroutine to crash mid-ticket.

**System response:**
1. Worker's `try/except Exception` block catches the error
2. First retry attempted after 2-second pause
3. If retry also fails → ticket goes to `dead_letter_queue`
4. `AuditLogger.log_dead_letter(ticket_id, error)` records it permanently
5. Dead-letter entries appear in `audit_log.json` under `summary.dead_letters`
6. Worker itself does **not** crash — it moves to next ticket in queue
7. Other 4 workers continue processing concurrently unaffected
8. Final summary clearly reports dead-letter count

**Code location:** `agent/orchestrator.py` → `_worker()` method, dead-letter logic

---

## Summary Table

| Failure | Probability | Retry? | Recovery | Worker impact |
|---------|-------------|--------|----------|---------------|
| Tool timeout | 15% per call | Yes, 3× backoff | Escalate | None |
| Malformed data | 8% per call | No (fast-fail) | Clarify/escalate | None |
| Invalid order ID | Deterministic | No | Decline + escalate | None |
| Social engineering | Deterministic | N/A | Flag + decline | None |
| Max steps reached | Edge case | N/A | Auto-escalate | None |
| Worker crash | Rare | 1× retry | Dead-letter queue | None |
