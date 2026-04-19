"""
generate_architecture.py — Generates architecture.png for submission.
Run: python generate_architecture.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe

fig, ax = plt.subplots(1, 1, figsize=(18, 11))
ax.set_xlim(0, 18)
ax.set_ylim(0, 11)
ax.set_aspect("equal")
ax.axis("off")
fig.patch.set_facecolor("#0d1117")
ax.set_facecolor("#0d1117")

# ── Color palette ─────────────────────────────────────────────────────────────
C = {
    "bg":       "#0d1117",
    "surface":  "#161b22",
    "surface2": "#21262d",
    "blue":     "#58a6ff",
    "teal":     "#39d353",
    "amber":    "#e3b341",
    "red":      "#f85149",
    "purple":   "#bc8cff",
    "pink":     "#f778ba",
    "text":     "#e6edf3",
    "text2":    "#8b949e",
    "border":   "#30363d",
    "green":    "#3fb950",
}

def box(ax, x, y, w, h, color, alpha=0.15, radius=0.15, lw=1.2):
    rect = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=lw,
        edgecolor=color,
        facecolor=color,
        alpha=alpha,
    )
    ax.add_patch(rect)
    border = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=lw,
        edgecolor=color,
        facecolor="none",
        alpha=0.9,
    )
    ax.add_patch(border)

def label(ax, x, y, text, size=8, color=C["text"], bold=False, ha="center", va="center"):
    ax.text(x, y, text,
            fontsize=size,
            color=color,
            fontweight="bold" if bold else "normal",
            ha=ha, va=va,
            fontfamily="monospace")

def arrow(ax, x1, y1, x2, y2, color=C["text2"], lw=1.2, style="->"):
    ax.annotate("",
        xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle=style,
            color=color,
            lw=lw,
            connectionstyle="arc3,rad=0.0",
        )
    )

def arrow_label(ax, x, y, text, color=C["text2"]):
    ax.text(x, y, text, fontsize=6.5, color=color, ha="center", va="center",
            fontfamily="monospace",
            bbox=dict(boxstyle="round,pad=0.15", facecolor=C["bg"], edgecolor="none"))

# ══════════════════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════════════════
ax.text(9, 10.6, "ShopWave — Autonomous Support Resolution Agent",
        fontsize=14, color=C["text"], fontweight="bold", ha="center",
        fontfamily="monospace")
ax.text(9, 10.3, "Architecture · LangGraph ReAct · asyncio Concurrency · Azure GPT-4o-mini",
        fontsize=8, color=C["text2"], ha="center", fontfamily="monospace")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 1 — INGEST
# ══════════════════════════════════════════════════════════════════════════════
# Source
box(ax, 0.3, 8.8, 2.4, 1.0, C["blue"])
label(ax, 1.5, 9.5, "📄 tickets.json", 8, C["blue"], bold=True)
label(ax, 1.5, 9.2, "20 support tickets", 7, C["text2"])
label(ax, 1.5, 9.0, "email / ticket_queue", 6.5, C["text2"])

# Validator
box(ax, 3.0, 8.8, 2.4, 1.0, C["teal"])
label(ax, 4.2, 9.5, "✅ Schema Validator", 8, C["teal"], bold=True)
label(ax, 4.2, 9.2, "Pydantic v2", 7, C["text2"])
label(ax, 4.2, 9.0, "reject malformed early", 6.5, C["text2"])

# Queue
box(ax, 5.7, 8.8, 2.4, 1.0, C["purple"])
label(ax, 6.9, 9.5, "⚡ asyncio.Queue", 8, C["purple"], bold=True)
label(ax, 6.9, 9.2, "all 20 tickets enqueued", 7, C["text2"])
label(ax, 6.9, 9.0, "non-blocking fan-out", 6.5, C["text2"])

arrow(ax, 2.7, 9.3, 3.0, 9.3, C["blue"])
arrow(ax, 5.4, 9.3, 5.7, 9.3, C["teal"])

# Section label
label(ax, 0.3, 10.05, "① INGEST", 7, C["text2"], ha="left")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 2 — WORKERS
# ══════════════════════════════════════════════════════════════════════════════
label(ax, 0.3, 8.55, "② CONCURRENT WORKERS (5 parallel)", 7, C["text2"], ha="left")

# fan-out arrow from queue to workers
arrow(ax, 6.9, 8.8, 6.9, 8.35, C["purple"])
arrow_label(ax, 7.4, 8.6, "fan-out")

workers = ["W01", "W02", "W03", "W04", "W05"]
wx = [1.2, 3.9, 6.6, 9.3, 12.0]
for i, (wid, x) in enumerate(zip(workers, wx)):
    box(ax, x, 7.5, 2.0, 0.75, C["amber"])
    label(ax, x+1.0, 7.95, f"🤖 {wid}", 8, C["amber"], bold=True)
    label(ax, x+1.0, 7.72, "asyncio coroutine", 6.5, C["text2"])
    # Arrow from queue to worker
    arrow(ax, 6.9, 8.35, x+1.0, 8.25, C["amber"], lw=0.9)

# dead letter box
box(ax, 14.5, 7.5, 3.2, 0.75, C["red"], alpha=0.12)
label(ax, 16.1, 7.95, "💀 Dead-Letter Queue", 8, C["red"], bold=True)
label(ax, 16.1, 7.72, "permanently failed tickets", 6.5, C["text2"])
arrow(ax, 14.0, 7.87, 14.5, 7.87, C["red"], lw=0.9)

# ══════════════════════════════════════════════════════════════════════════════
# ROW 3 — LANGGRAPH NODES
# ══════════════════════════════════════════════════════════════════════════════
label(ax, 0.3, 7.25, "③ LANGGRAPH AGENT GRAPH (per ticket)", 7, C["text2"], ha="left")

# Arrow: workers → graph
for x in wx:
    arrow(ax, x+1.0, 7.5, x+1.0, 7.15, C["amber"], lw=0.7)

# Classify node
box(ax, 0.3, 6.1, 3.0, 1.0, C["teal"])
label(ax, 1.8, 6.75, "🏷  classify_node", 8.5, C["teal"], bold=True)
label(ax, 1.8, 6.48, "LLM triage: category,", 7, C["text2"])
label(ax, 1.8, 6.28, "urgency, confidence 0–1", 7, C["text2"])

# React loop node
box(ax, 4.2, 6.1, 4.5, 1.0, C["blue"])
label(ax, 6.45, 6.75, "🔄 react_step_node  (loops)", 8.5, C["blue"], bold=True)
label(ax, 6.45, 6.48, "Thought → Act → Observe → repeat", 7, C["text2"])
label(ax, 6.45, 6.28, "min 3 tool calls enforced", 7, C["text2"])

# Finalize node
box(ax, 9.6, 6.1, 3.0, 1.0, C["green"])
label(ax, 11.1, 6.75, "🏁 finalize_node", 8.5, C["green"], bold=True)
label(ax, 11.1, 6.48, "safety net: if no outcome", 7, C["text2"])
label(ax, 11.1, 6.28, "auto-escalate before END", 7, C["text2"])

arrow(ax, 3.3, 6.6, 4.2, 6.6, C["teal"])
arrow(ax, 8.7, 6.6, 9.6, 6.6, C["blue"])
# Loop arrow (self-loop on react_step)
ax.annotate("",
    xy=(8.55, 6.2), xytext=(8.55, 5.82),
    arrowprops=dict(arrowstyle="->", color=C["blue"], lw=1.0,
                    connectionstyle="arc3,rad=0.0"))
ax.annotate("",
    xy=(4.35, 5.82), xytext=(8.55, 5.82),
    arrowprops=dict(arrowstyle="-", color=C["blue"], lw=1.0))
ax.annotate("",
    xy=(4.35, 6.2), xytext=(4.35, 5.82),
    arrowprops=dict(arrowstyle="->", color=C["blue"], lw=1.0))
arrow_label(ax, 6.45, 5.72, "loop until done | max_steps")

# Conditional routing label
label(ax, 9.15, 6.95, "should_continue()", 6.5, C["text2"])

# ══════════════════════════════════════════════════════════════════════════════
# ROW 4 — TOOLS
# ══════════════════════════════════════════════════════════════════════════════
label(ax, 0.3, 5.55, "④ TOOL LAYER", 7, C["text2"], ha="left")
arrow(ax, 6.45, 6.1, 6.45, 5.35, C["blue"])

# READ tools
read_tools = ["get_order()", "get_customer()", "get_product()", "search_kb()"]
rx = [0.3, 2.55, 4.8, 7.05]
for t, x in zip(read_tools, rx):
    box(ax, x, 4.6, 2.1, 0.65, C["teal"], alpha=0.12)
    label(ax, x+1.05, 4.97, t, 7.5, C["teal"], bold=True)
    label(ax, x+1.05, 4.74, "READ · chaos injected", 6, C["text2"])

# WRITE tools
write_tools = ["check_refund()", "issue_refund()", "send_reply()", "escalate()"]
wx2 = [9.4, 11.5, 13.6, 15.7]
for t, x in zip(write_tools, wx2):
    box(ax, x, 4.6, 1.95, 0.65, C["red"], alpha=0.12)
    label(ax, x+0.975, 4.97, t, 7.5, C["red"], bold=True)
    color2 = C["red"] if t == "issue_refund()" else C["text2"]
    sub = "IRREVERSIBLE⚠" if t == "issue_refund()" else "WRITE · action"
    label(ax, x+0.975, 4.74, sub, 6, color2)

# Safety gate annotation
box(ax, 10.6, 4.0, 2.8, 0.5, C["red"], alpha=0.08, radius=0.1)
label(ax, 12.0, 4.25, "🛡 SAFETY GATE", 7.5, C["red"], bold=True)
label(ax, 12.0, 4.07, "eligibility must be confirmed first", 6, C["text2"])
arrow(ax, 12.0, 4.6, 12.0, 4.5, C["red"], lw=0.8)

# Retry/timeout decorator annotation
box(ax, 0.3, 3.65, 9.0, 0.65, C["purple"], alpha=0.08, radius=0.1)
label(ax, 4.8, 4.05, "@tool_call decorator — asyncio.wait_for(8s) · exponential backoff 1s→2s→4s · ToolResult schema · chaos injection", 7, C["purple"])

# ══════════════════════════════════════════════════════════════════════════════
# ROW 5 — OUTCOMES
# ══════════════════════════════════════════════════════════════════════════════
label(ax, 0.3, 3.35, "⑤ OUTCOMES", 7, C["text2"], ha="left")

outcomes = [
    ("✅ auto_resolved",    C["green"]),
    ("ℹ️  informational",    C["blue"]),
    ("⬆️  escalated",        C["amber"]),
    ("🚩 flagged",          C["red"]),
    ("❓ clarify",          C["purple"]),
    ("🚫 cancelled",        C["teal"]),
]
ox = [0.3, 3.0, 5.7, 8.4, 11.1, 13.8]
for (text, color), x in zip(outcomes, ox):
    box(ax, x, 2.7, 2.5, 0.5, color, alpha=0.15, radius=0.1)
    label(ax, x+1.25, 2.95, text, 7.5, color, bold=True)

# ══════════════════════════════════════════════════════════════════════════════
# ROW 6 — AUDIT
# ══════════════════════════════════════════════════════════════════════════════
label(ax, 0.3, 2.45, "⑥ AUDIT", 7, C["text2"], ha="left")

box(ax, 0.3, 1.5, 5.5, 0.85, C["purple"])
label(ax, 3.05, 2.05, "📋 AuditLogger → audit_log.json", 9, C["purple"], bold=True)
label(ax, 3.05, 1.78, "ticket_id · tool_calls · thought · observation · outcome · confidence · duration_ms", 6.5, C["text2"])

box(ax, 6.3, 1.5, 4.0, 0.85, C["blue"])
label(ax, 8.3, 2.05, "🚀 FastAPI + SSE", 9, C["blue"], bold=True)
label(ax, 8.3, 1.78, "POST /run · GET /stream · GET /results · GET /", 6.5, C["text2"])

box(ax, 10.8, 1.5, 3.5, 0.85, C["teal"])
label(ax, 12.55, 2.05, "📊 Live Dashboard", 9, C["teal"], bold=True)
label(ax, 12.55, 1.78, "dark-theme · SSE real-time updates", 6.5, C["text2"])

box(ax, 14.8, 1.5, 2.9, 0.85, C["amber"])
label(ax, 16.25, 2.05, "🐳 Docker", 9, C["amber"], bold=True)
label(ax, 16.25, 1.78, "Dockerfile + compose", 6.5, C["text2"])

# Arrows from outcomes to audit
for x in ox:
    arrow(ax, x+1.25, 2.7, x+1.25, 2.55, C["text2"], lw=0.6)

# Connect audit to main log
arrow(ax, 3.05, 2.7, 3.05, 2.35, C["purple"], lw=0.9)

# ══════════════════════════════════════════════════════════════════════════════
# LEGEND
# ══════════════════════════════════════════════════════════════════════════════
legend_items = [
    ("READ tool", C["teal"]),
    ("WRITE tool", C["red"]),
    ("LangGraph node", C["blue"]),
    ("Concurrency", C["amber"]),
    ("Audit/Infra", C["purple"]),
]
lx = 0.3
for name, color in legend_items:
    patch = mpatches.Patch(facecolor=color, alpha=0.4, edgecolor=color, label=name)
    ax.add_patch(FancyBboxPatch((lx, 0.25), 0.25, 0.25,
                                boxstyle="round,pad=0",
                                facecolor=color, alpha=0.5, edgecolor=color))
    label(ax, lx+0.45, 0.37, name, 6.5, C["text2"], ha="left")
    lx += 2.1

ax.text(17.7, 0.25, "KSOLVES · Agentic AI Hackathon 2026",
        fontsize=6.5, color=C["text2"], ha="right", fontfamily="monospace")

plt.tight_layout(pad=0.2)
plt.savefig("architecture.png", dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
print("✅ architecture.png generated")
