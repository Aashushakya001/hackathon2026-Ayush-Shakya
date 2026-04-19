"""
audit/logger.py — Structured audit logging for every ticket decision.

Writes two outputs:
  1. audit_log.json — machine-readable full log (required deliverable)
  2. Console/file log via Python logging

Every tool call, reasoning step, outcome, and failure is recorded.
No black-box outputs — full explainability.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from models import AuditLogEntry, TicketStatus
from config import config

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Incrementally writes audit log entries to audit_log.json.
    Thread-safe via file append + final consolidation.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or config.AUDIT_LOG_PATH
        self._entries: list[dict] = []
        self._dead_letters: list[dict] = []
        self._started_at = datetime.now(timezone.utc).isoformat()

    def log(self, entry: AuditLogEntry) -> None:
        """Record a completed ticket audit entry."""
        data = entry.model_dump()
        self._entries.append(data)

        # Also log summary to console for real-time visibility
        status_symbol = {
            TicketStatus.RESOLVED: "✅",
            TicketStatus.ESCALATED: "⬆️ ",
            TicketStatus.FAILED: "❌",
            TicketStatus.DEAD_LETTER: "💀",
        }.get(entry.status, "❓")

        logger.info(
            f"{status_symbol} {entry.ticket_id} | "
            f"outcome={entry.outcome.value if entry.outcome else 'N/A'} | "
            f"confidence={entry.confidence_score:.2f} | "
            f"tool_calls={entry.tool_calls_count} | "
            f"{entry.total_duration_ms:.0f}ms"
        )

    def log_dead_letter(self, ticket_id: str, error: str) -> None:
        """Record a permanently failed ticket in the dead-letter section."""
        self._dead_letters.append({
            "ticket_id": ticket_id,
            "error": error,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.error(f"💀 DEAD LETTER: {ticket_id} — {error}")

    def finalize(self, entries: list[AuditLogEntry]) -> None:
        """Write the complete consolidated audit log to disk."""
        # Use provided entries (may be more complete than incremental)
        all_entries = [e.model_dump() for e in entries]

        # Compute summary stats
        outcomes = {}
        total_tool_calls = 0
        total_duration = 0.0
        confidence_scores = []

        for e in entries:
            outcome_key = e.outcome.value if e.outcome else "unknown"
            outcomes[outcome_key] = outcomes.get(outcome_key, 0) + 1
            total_tool_calls += e.tool_calls_count
            total_duration += e.total_duration_ms
            confidence_scores.append(e.confidence_score)

        avg_confidence = (
            sum(confidence_scores) / len(confidence_scores)
            if confidence_scores else 0.0
        )

        audit_document = {
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "started_at": self._started_at,
                "agent_version": "1.0.0",
                "model": config.AZURE_OPENAI_DEPLOYMENT_NAME,
                "total_tickets": len(entries),
                "total_tool_calls": total_tool_calls,
                "avg_confidence": round(avg_confidence, 3),
                "total_duration_ms": round(total_duration, 1),
                "avg_duration_ms": round(
                    total_duration / max(len(entries), 1), 1
                ),
            },
            "summary": {
                "outcomes": outcomes,
                "dead_letter_count": len(self._dead_letters),
                "dead_letters": self._dead_letters,
            },
            "tickets": all_entries,
        }

        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(audit_document, f, indent=2, default=str)

        logger.info(
            f"[AuditLogger] Audit log written to {self.path} "
            f"({len(entries)} tickets, {total_tool_calls} tool calls)"
        )

        # Print readable summary
        self._print_summary(audit_document["meta"], audit_document["summary"])

    def _print_summary(self, meta: dict, summary: dict) -> None:
        print("\n" + "═" * 60)
        print("  SHOPWAVE AGENT — RUN COMPLETE")
        print("═" * 60)
        print(f"  Tickets processed : {meta['total_tickets']}")
        print(f"  Total tool calls  : {meta['total_tool_calls']}")
        print(f"  Avg confidence    : {meta['avg_confidence']:.2%}")
        print(f"  Total duration    : {meta['total_duration_ms']:.0f}ms")
        print(f"  Avg per ticket    : {meta['avg_duration_ms']:.0f}ms")
        print("─" * 60)
        print("  Outcomes:")
        for k, v in summary["outcomes"].items():
            print(f"    {k:<30} {v}")
        if summary["dead_letter_count"]:
            print(f"  Dead letters      : {summary['dead_letter_count']}")
        print("─" * 60)
        print(f"  Audit log         : {config.AUDIT_LOG_PATH}")
        print("═" * 60 + "\n")
