"""
agent/orchestrator.py — Concurrent ticket processing orchestrator.

Architecture:
  - Loads all 20 tickets
  - Pushes them into an asyncio.Queue
  - Spins up MAX_WORKERS coroutines that pull and process concurrently
  - Failed tickets go to dead-letter queue (never silently dropped)
  - All results are collected into audit_log.json
  - Real-time progress events emitted via asyncio.Queue for SSE dashboard
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, Callable

from models import AuditLogEntry, TicketStatus
from agent.react_loop import process_ticket
from audit.logger import AuditLogger
from config import config

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Manages concurrent ticket processing across N workers.
    
    Usage:
        orch = Orchestrator()
        results = await orch.run(progress_callback=my_fn)
    """

    def __init__(self):
        self.ticket_queue: asyncio.Queue = asyncio.Queue()
        self.dead_letter_queue: asyncio.Queue = asyncio.Queue()
        self.results: list[AuditLogEntry] = []
        self.audit_logger = AuditLogger()
        self._progress_callbacks: list[Callable] = []
        self._stats = {
            "total": 0,
            "processed": 0,
            "resolved": 0,
            "escalated": 0,
            "failed": 0,
            "dead_letter": 0,
            "started_at": None,
        }

    def add_progress_callback(self, fn: Callable) -> None:
        """Register a callback fired after each ticket completes."""
        self._progress_callbacks.append(fn)

    async def _emit_progress(self, event: dict) -> None:
        for cb in self._progress_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event)
                else:
                    cb(event)
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")

    def _load_tickets(self) -> list[dict]:
        path = os.path.join(config.DATA_DIR, "tickets.json")
        with open(path, encoding="utf-8") as f:
            tickets = json.load(f)
        logger.info(f"[Orchestrator] Loaded {len(tickets)} tickets")
        return tickets

    async def _worker(self, worker_id: str) -> None:
        """
        Worker coroutine: pulls tickets from queue and processes them.
        On failure, puts ticket into dead-letter queue.
        Runs until queue is exhausted (sentinel None received).
        """
        logger.info(f"[{worker_id}] Worker started")

        while True:
            ticket = await self.ticket_queue.get()

            # Sentinel value signals shutdown
            if ticket is None:
                self.ticket_queue.task_done()
                logger.info(f"[{worker_id}] Worker shutting down")
                break

            ticket_id = ticket.get("ticket_id", "UNKNOWN")
            attempt = 0
            max_attempts = 2  # One retry before dead-letter

            await self._emit_progress({
                "event": "ticket_started",
                "ticket_id": ticket_id,
                "worker_id": worker_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            while attempt < max_attempts:
                try:
                    entry = await process_ticket(ticket, worker_id)
                    self.results.append(entry)
                    self.audit_logger.log(entry)

                    # Update stats
                    self._stats["processed"] += 1
                    if entry.status == TicketStatus.RESOLVED:
                        self._stats["resolved"] += 1
                    elif entry.status == TicketStatus.ESCALATED:
                        self._stats["escalated"] += 1
                    elif entry.status == TicketStatus.FAILED:
                        self._stats["failed"] += 1

                    await self._emit_progress({
                        "event": "ticket_completed",
                        "ticket_id": ticket_id,
                        "worker_id": worker_id,
                        "outcome": entry.outcome.value if entry.outcome else "unknown",
                        "status": entry.status.value,
                        "confidence": entry.confidence_score,
                        "tool_calls": entry.tool_calls_count,
                        "duration_ms": entry.total_duration_ms,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stats": dict(self._stats),
                    })
                    break

                except Exception as e:
                    attempt += 1
                    logger.error(
                        f"[{worker_id}] {ticket_id} attempt {attempt} failed: {e}"
                    )
                    if attempt >= max_attempts:
                        # Dead-letter: ticket permanently failed
                        await self.dead_letter_queue.put({
                            "ticket_id": ticket_id,
                            "ticket": ticket,
                            "error": str(e),
                            "failed_at": datetime.now(timezone.utc).isoformat(),
                            "worker_id": worker_id,
                        })
                        self._stats["dead_letter"] += 1
                        self.audit_logger.log_dead_letter(ticket_id, str(e))

                        await self._emit_progress({
                            "event": "ticket_dead_letter",
                            "ticket_id": ticket_id,
                            "worker_id": worker_id,
                            "error": str(e),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    else:
                        await asyncio.sleep(2)  # Brief pause before retry

            self.ticket_queue.task_done()

    async def run(
        self,
        tickets: Optional[list[dict]] = None,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Main entry point. Loads tickets, spins workers, waits for completion.
        Returns summary stats and path to audit log.
        """
        if progress_callback:
            self.add_progress_callback(progress_callback)

        tickets = tickets or self._load_tickets()
        self._stats["total"] = len(tickets)
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"[Orchestrator] Starting {len(tickets)} tickets "
            f"with {config.MAX_WORKERS} workers"
        )

        start_time = time.monotonic()

        # Enqueue all tickets
        for ticket in tickets:
            await self.ticket_queue.put(ticket)

        # Enqueue sentinel values to shut down workers
        for _ in range(config.MAX_WORKERS):
            await self.ticket_queue.put(None)

        # Start all workers concurrently
        worker_tasks = [
            asyncio.create_task(
                self._worker(f"W{i+1:02d}"),
                name=f"worker-{i+1}",
            )
            for i in range(config.MAX_WORKERS)
        ]

        await self._emit_progress({
            "event": "batch_started",
            "total_tickets": len(tickets),
            "workers": config.MAX_WORKERS,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Wait for all workers to complete
        await asyncio.gather(*worker_tasks)

        total_duration = (time.monotonic() - start_time) * 1000

        # Drain dead-letter queue for final report
        dead_letters = []
        while not self.dead_letter_queue.empty():
            dead_letters.append(await self.dead_letter_queue.get())

        # Write final audit log
        self.audit_logger.finalize(self.results)

        summary = {
            "total_tickets": len(tickets),
            "processed": self._stats["processed"],
            "resolved": self._stats["resolved"],
            "escalated": self._stats["escalated"],
            "failed": self._stats["failed"],
            "dead_letter": self._stats["dead_letter"],
            "total_duration_ms": total_duration,
            "avg_duration_ms": total_duration / max(len(tickets), 1),
            "audit_log_path": config.AUDIT_LOG_PATH,
            "dead_letters": dead_letters,
        }

        await self._emit_progress({
            "event": "batch_completed",
            "summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"[Orchestrator] COMPLETE — "
            f"{summary['processed']}/{summary['total_tickets']} processed, "
            f"{summary['resolved']} resolved, "
            f"{summary['escalated']} escalated, "
            f"{summary['dead_letter']} dead-lettered, "
            f"total={total_duration:.0f}ms"
        )

        return summary
