"""
models.py — All Pydantic data models for the agent.
Every tool input/output is validated against these schemas.
"""
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ─────────────────────────── Enums ───────────────────────────

class TicketStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class OutcomeType(str, Enum):
    AUTO_RESOLVED = "auto_resolved"
    INFORMATIONAL = "informational"
    ESCALATED = "escalated"
    FLAGGED = "flagged"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CANCELLED = "cancelled"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CustomerTier(str, Enum):
    STANDARD = "standard"
    PREMIUM = "premium"
    VIP = "vip"


# ─────────────────────────── Input Models ───────────────────────────

class Ticket(BaseModel):
    ticket_id: str
    customer_email: str
    subject: str
    body: str
    source: str
    created_at: str
    tier: int
    expected_action: Optional[str] = None


class Order(BaseModel):
    order_id: str
    customer_id: str
    product_id: str
    quantity: int
    amount: float
    status: str
    order_date: str
    delivery_date: Optional[str] = None
    return_deadline: Optional[str] = None
    refund_status: Optional[str] = None
    notes: Optional[str] = None


class Customer(BaseModel):
    customer_id: str
    name: str
    email: str
    phone: Optional[str] = None
    tier: str
    member_since: str
    total_orders: int
    total_spent: float
    notes: Optional[str] = None


class Product(BaseModel):
    product_id: str
    name: str
    category: str
    price: float
    warranty_months: int
    return_window_days: int
    returnable: bool
    notes: Optional[str] = None


class KnowledgeEntry(BaseModel):
    id: str
    topic: str
    content: str


# ─────────────────────────── Tool Result ───────────────────────────

class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    error_type: Optional[str] = None  # timeout | malformed | not_found | validation_error
    retries_used: int = 0
    duration_ms: float = 0.0


# ─────────────────────────── ReAct Step ───────────────────────────

class ReActStep(BaseModel):
    step_number: int
    thought: str
    action: str  # tool name
    action_input: dict
    observation: str
    tool_result: Optional[ToolResult] = None


# ─────────────────────────── Audit Log Entry ───────────────────────────

class AuditLogEntry(BaseModel):
    ticket_id: str
    customer_email: str
    subject: str
    status: TicketStatus
    outcome: Optional[OutcomeType] = None
    confidence_score: float = 0.0
    react_steps: list[ReActStep] = Field(default_factory=list)
    tool_calls_count: int = 0
    customer_reply: Optional[str] = None
    escalation_summary: Optional[str] = None
    escalation_priority: Optional[Priority] = None
    flags: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    processing_started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    processing_completed_at: Optional[str] = None
    total_duration_ms: float = 0.0
    worker_id: Optional[str] = None


# ─────────────────────────── Triage Result ───────────────────────────

class TriageResult(BaseModel):
    category: str           # refund | return | exchange | cancellation | tracking | warranty | policy | fraud | ambiguous
    urgency: str            # high | medium | low
    resolvability: str      # auto | escalate | clarify
    confidence: float       # 0.0 – 1.0
    order_id_extracted: Optional[str] = None
    flags: list[str] = Field(default_factory=list)
    reasoning: str
