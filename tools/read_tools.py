"""
tools/read_tools.py — All READ/LOOKUP tools.

Each tool:
  - Loads from local JSON (simulated DB)
  - Has realistic failure injection (chaos engineering)
  - Is wrapped with @tool_call for retry + timeout + ToolResult
  - Is explainable — every decision logged
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from models import ToolResult, Order, Customer, Product, KnowledgeEntry
from tools.base import tool_call, inject_realistic_failure
from config import config

logger = logging.getLogger(__name__)

# ─── Load data stores on module import ───────────────────────────────────────

def _load(filename: str) -> list:
    path = os.path.join(config.DATA_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_ORDERS: dict[str, dict] = {o["order_id"]: o for o in _load("orders.json")}
_CUSTOMERS: dict[str, dict] = {c["email"]: c for c in _load("customers.json")}
_CUSTOMERS_BY_ID: dict[str, dict] = {c["customer_id"]: c for c in _load("customers.json")}
_PRODUCTS: dict[str, dict] = {p["product_id"]: p for p in _load("products.json")}
_KB: list[dict] = _load("knowledge_base.json")

# ─── Tool: get_order ─────────────────────────────────────────────────────────

@tool_call("get_order", timeout=8.0)
@inject_realistic_failure(failure_rate=0.12, malformed_rate=0.08)
async def get_order(order_id: str) -> ToolResult:
    """
    Fetch order details, status, and timestamps by order_id.
    Raises KeyError if order does not exist.
    """
    await asyncio.sleep(0.05)  # simulate network latency

    if order_id not in _ORDERS:
        raise KeyError(f"Order '{order_id}' not found in system")

    raw = _ORDERS[order_id]
    order = Order(**raw)

    logger.info(f"[get_order] {order_id} → status={order.status}")
    return ToolResult(
        tool_name="get_order",
        success=True,
        data=order.model_dump(),
    )


# ─── Tool: get_customer ───────────────────────────────────────────────────────

@tool_call("get_customer", timeout=8.0)
@inject_realistic_failure(failure_rate=0.10, malformed_rate=0.06)
async def get_customer(email: str) -> ToolResult:
    """
    Fetch customer profile, tier, and history by email address.
    Raises KeyError if customer is not found.
    """
    await asyncio.sleep(0.03)

    if email not in _CUSTOMERS:
        raise KeyError(f"Customer with email '{email}' not found")

    raw = _CUSTOMERS[email]
    customer = Customer(**raw)

    logger.info(
        f"[get_customer] {email} → tier={customer.tier}, "
        f"orders={customer.total_orders}"
    )
    return ToolResult(
        tool_name="get_customer",
        success=True,
        data=customer.model_dump(),
    )


async def get_customer_by_id(customer_id: str) -> ToolResult:
    """Internal helper — no chaos injection."""
    if customer_id not in _CUSTOMERS_BY_ID:
        return ToolResult(
            tool_name="get_customer_by_id",
            success=False,
            error=f"Customer '{customer_id}' not found",
            error_type="not_found",
        )
    return ToolResult(
        tool_name="get_customer_by_id",
        success=True,
        data=Customer(**_CUSTOMERS_BY_ID[customer_id]).model_dump(),
    )


# ─── Tool: get_orders_by_email ────────────────────────────────────────────────

@tool_call("get_orders_by_email", timeout=8.0)
async def get_orders_by_email(email: str) -> ToolResult:
    """
    Find all orders associated with a customer email.
    Used when customer doesn't provide an order ID.
    """
    await asyncio.sleep(0.04)

    if email not in _CUSTOMERS:
        raise KeyError(f"Customer with email '{email}' not found")

    customer_id = _CUSTOMERS[email]["customer_id"]
    orders = [
        Order(**o).model_dump()
        for o in _ORDERS.values()
        if o["customer_id"] == customer_id
    ]

    logger.info(
        f"[get_orders_by_email] {email} → found {len(orders)} orders"
    )
    return ToolResult(
        tool_name="get_orders_by_email",
        success=True,
        data={"orders": orders, "count": len(orders)},
    )


# ─── Tool: get_product ────────────────────────────────────────────────────────

@tool_call("get_product", timeout=6.0)
@inject_realistic_failure(failure_rate=0.06, malformed_rate=0.04)
async def get_product(product_id: str) -> ToolResult:
    """
    Fetch product metadata, category, price, warranty, and return window.
    """
    await asyncio.sleep(0.02)

    if product_id not in _PRODUCTS:
        raise KeyError(f"Product '{product_id}' not found")

    raw = _PRODUCTS[product_id]
    product = Product(**raw)

    logger.info(
        f"[get_product] {product_id} → {product.name}, "
        f"return_window={product.return_window_days}d, "
        f"warranty={product.warranty_months}mo"
    )
    return ToolResult(
        tool_name="get_product",
        success=True,
        data=product.model_dump(),
    )


# ─── Tool: search_knowledge_base ─────────────────────────────────────────────

@tool_call("search_knowledge_base", timeout=6.0)
async def search_knowledge_base(query: str) -> ToolResult:
    """
    Semantic-style search of the ShopWave policy & FAQ knowledge base.
    Uses keyword matching (simulated embedding search for the hackathon).
    Returns top 3 most relevant entries.
    """
    await asyncio.sleep(0.03)

    query_lower = query.lower()

    # Score each KB entry by keyword overlap
    scored: list[tuple[float, dict]] = []
    for entry in _KB:
        text = (entry["topic"] + " " + entry["content"]).lower()
        query_words = set(query_lower.split())
        text_words = set(text.split())
        overlap = len(query_words & text_words)
        # Boost for topic keyword match
        topic_match = any(w in entry["topic"] for w in query_lower.split("_"))
        score = overlap + (5 if topic_match else 0)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_results = [e for _, e in scored[:3]]

    logger.info(
        f"[search_knowledge_base] query='{query}' → "
        f"{len(top_results)} results"
    )

    if not top_results:
        return ToolResult(
            tool_name="search_knowledge_base",
            success=True,
            data={"results": [], "message": "No relevant policy found for query"},
        )

    return ToolResult(
        tool_name="search_knowledge_base",
        success=True,
        data={"results": top_results, "count": len(top_results)},
    )
