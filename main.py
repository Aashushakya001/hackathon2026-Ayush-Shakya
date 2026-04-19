"""
main.py — Single command entry point for the ShopWave Support Agent.

Usage:
  python main.py          → CLI mode: process all 20 tickets, write audit_log.json
  python main.py --serve  → Start FastAPI server with live dashboard on :8000
  python main.py --ticket TKT-001  → Process a single ticket (debug mode)

This is the file judges will run.
"""
from __future__ import annotations
import os, sys
# Fix Windows console encoding so UTF-8 output works on all terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


import argparse
import asyncio
import json
import logging
import os
import sys

from config import config, Config


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)-25s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("agent.log", mode="a"),
        ],
    )
    # Reduce noise from httpx/openai internals
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run_cli() -> int:
    """Run all 20 tickets concurrently and write audit_log.json."""
    from agent.orchestrator import Orchestrator

    print("\n" + "═" * 60)
    print("  SHOPWAVE AUTONOMOUS SUPPORT RESOLUTION AGENT")
    print("  Agentic AI Hackathon 2026 — KSOLVES")
    print("═" * 60)
    print(f"  Model    : {config.AZURE_OPENAI_DEPLOYMENT_NAME}")
    print(f"  Workers  : {config.MAX_WORKERS}")
    print(f"  Max steps: {config.MAX_REACT_STEPS}")
    print(f"  Audit log: {config.AUDIT_LOG_PATH}")
    print("═" * 60 + "\n")

    orch = Orchestrator()
    summary = await orch.run()

    print(f"\n✅ Done. Audit log written to: {config.AUDIT_LOG_PATH}")
    return 0 if summary["dead_letter"] == 0 else 1


async def run_single_ticket(ticket_id: str) -> None:
    """Debug mode: process one specific ticket and print result."""
    import json as _json
    from agent.react_loop import process_ticket

    tickets_path = os.path.join(config.DATA_DIR, "tickets.json")
    with open(tickets_path, encoding="utf-8") as f:
        tickets = _json.load(f)

    ticket = next((t for t in tickets if t["ticket_id"] == ticket_id), None)
    if not ticket:
        print(f"❌ Ticket {ticket_id} not found")
        return

    print(f"\n🔍 Processing {ticket_id} in debug mode...\n")
    entry = await process_ticket(ticket, worker_id="DEBUG")   

    print("\n" + "─" * 60)
    print(f"TICKET  : {entry.ticket_id}")
    print(f"OUTCOME : {entry.outcome.value if entry.outcome else 'N/A'}")
    print(f"STATUS  : {entry.status.value}")
    print(f"CONFIDENCE: {entry.confidence_score:.2%}")
    print(f"TOOL CALLS: {entry.tool_calls_count}")
    print(f"DURATION  : {entry.total_duration_ms:.0f}ms")
    print(f"FLAGS     : {entry.flags}")
    print("\nREASONING STEPS:")
    for step in entry.react_steps:
        print(f"\n  Step {step.step_number}:")
        print(f"    💭 {step.thought[:200]}")
        print(f"    🔧 {step.action}({json.dumps(step.action_input)})")
        print(f"    👁  {step.observation[:200]}")
    if entry.customer_reply:
        print(f"\nREPLY TO CUSTOMER:\n{entry.customer_reply}")
    if entry.escalation_summary:
        print(f"\nESCALATION SUMMARY:\n{entry.escalation_summary}")
    print("─" * 60)


def run_server() -> None:
    """Start FastAPI server with live dashboard."""
    import uvicorn
    print("\n🚀 Starting ShopWave Agent Dashboard...")
    print("   Open http://localhost:8000 in your browser")
    print("   Press CTRL+C to stop\n")
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="warning",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ShopWave Autonomous Support Resolution Agent"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start FastAPI server with live dashboard on port 8000",
    )
    parser.add_argument(
        "--ticket",
        type=str,
        metavar="TICKET_ID",
        help="Process a single ticket in debug mode (e.g. TKT-001)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    setup_logging(args.log_level)

    # Validate credentials
    try:
        Config.validate()
    except EnvironmentError as e:
        print(f"\n❌ Configuration Error:\n{e}\n")
        sys.exit(1)

    if args.serve:
        run_server()
    elif args.ticket:
        asyncio.run(run_single_ticket(args.ticket))
    else:
        exit_code = asyncio.run(run_cli())
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
