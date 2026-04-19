"""
api/server.py — FastAPI server with real-time SSE dashboard.

Endpoints:
  GET  /           → serves the live dashboard HTML
  POST /run        → starts processing all 20 tickets
  GET  /stream     → SSE stream of live progress events
  GET  /results    → returns full audit_log.json
  GET  /status     → current run status and stats
  GET  /health     → health check
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent.orchestrator import Orchestrator
from config import config

logger = logging.getLogger(__name__)

app = FastAPI(
    title="ShopWave Support Agent",
    description="Autonomous Support Resolution Agent — Agentic AI Hackathon 2026",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Global State ─────────────────────────────────────────────────────────────

_run_lock = asyncio.Lock()
_is_running = False
_event_queue: asyncio.Queue = asyncio.Queue()
_run_summary: Optional[dict] = None
_subscribers: list[asyncio.Queue] = []


async def _broadcast(event: dict) -> None:
    """Broadcast an event to all SSE subscribers."""
    global _run_summary
    if event.get("event") == "batch_completed":
        _run_summary = event.get("summary")

    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/status")
async def status():
    return {
        "is_running": _is_running,
        "summary": _run_summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/run")
async def run_agent():
    """Start processing all 20 tickets concurrently."""
    global _is_running, _run_summary

    if _is_running:
        raise HTTPException(
            status_code=409,
            detail="Agent is already running. Wait for current batch to complete.",
        )

    async with _run_lock:
        _is_running = True
        _run_summary = None

    async def _run_in_background():
        global _is_running
        try:
            orch = Orchestrator()
            summary = await orch.run(progress_callback=_broadcast)
            logger.info(f"[API] Run complete: {summary}")
        except Exception as e:
            logger.error(f"[API] Run failed: {e}")
            await _broadcast({
                "event": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            _is_running = False

    asyncio.create_task(_run_in_background())

    return {
        "status": "started",
        "message": f"Processing tickets with {config.MAX_WORKERS} workers",
        "stream_url": "/stream",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/stream")
async def stream_events():
    """
    Server-Sent Events endpoint for real-time progress.
    Dashboard connects here to receive live updates.
    """
    subscriber_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.append(subscriber_queue)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Send current status immediately on connect
            yield f"data: {json.dumps({'event': 'connected', 'is_running': _is_running, 'timestamp': datetime.now(timezone.utc).isoformat()})}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(
                        subscriber_queue.get(), timeout=30.0
                    )
                    yield f"data: {json.dumps(event, default=str)}\n\n"

                    if event.get("event") == "batch_completed":
                        break

                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield f"data: {json.dumps({'event': 'heartbeat', 'timestamp': datetime.now(timezone.utc).isoformat()})}\n\n"

        except asyncio.CancelledError:
            pass
        finally:
            if subscriber_queue in _subscribers:
                _subscribers.remove(subscriber_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/results")
async def get_results():
    """Return the full audit log JSON."""
    if not os.path.exists(config.AUDIT_LOG_PATH):
        raise HTTPException(
            status_code=404,
            detail="No audit log found. Run the agent first via POST /run",
        )
    with open(config.AUDIT_LOG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return JSONResponse(content=data)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the live real-time dashboard."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "static", "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Dashboard not found. Run from project root.</h1>")
