"""Backend (plain Python) for the sales agent.

The Streamlit app (app.py) only calls the functions in this file.
Nothing here uses input()/print() for interaction, so it works behind any UI.
"""
import json
import os
import sqlite3
from pathlib import Path
from typing import Annotated, TypedDict
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

load_dotenv()

BASE_DIR = Path(__file__).parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "sales.db"))

_model = None


def get_model():
    """Create the Groq model lazily, so importing this file never fails without a key."""
    global _model
    if _model is None:
        _model = ChatOpenAI(
            model="openai/gpt-oss-120b",  # Groq model name
            openai_api_key=os.getenv("GROQ_API_KEY"),
            openai_api_base="https://api.groq.com/openai/v1",
            temperature=0,
        )
    return _model


# ---------------------------------------------------------------- database
def query_database(q: dict) -> list:
    sql = "SELECT id, vehicle, year, product_type, name, price FROM products WHERE 1=1"
    params = []

    if q.get("vehicle"):
        sql += " AND LOWER(vehicle) LIKE ?"
        params.append("%" + str(q["vehicle"]).lower().strip() + "%")
    if q.get("year"):
        try:
            year = int(q["year"])
            sql += " AND year = ?"
            params.append(year)
        except (TypeError, ValueError):
            pass
    if q.get("product_type") in ("spare_part", "accessory"):
        sql += " AND product_type = ?"
        params.append(q["product_type"])
    if q.get("item"):
        item = str(q["item"]).lower().strip()
        if item.endswith("s"):  # 'brake pads' -> 'brake pad'
            item = item[:-1]
        sql += " AND LOWER(name) LIKE ?"
        params.append("%" + item + "%")
    if q.get("max_price"):
        try:
            max_price = float(q["max_price"])
            sql += " AND price <= ?"
            params.append(max_price)
        except (TypeError, ValueError):
            pass

    sql += " ORDER BY price"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def format_products(products: list) -> str:
    return "\n".join(
        f"- {p['name']} ({p['vehicle']} {p['year']}, {p['product_type']}) - price: {p['price']}"
        for p in products
    )


# ------------------------------------------- LangGraph: find + recommend
class State(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    customer_need: str
    query: dict
    products: list
    recommendation: str


def understand_customer(state: State):
    user_text = state["messages"][-1].content
    resp = get_model().invoke(
        "Extract what the customer wants from the message. Reply with JSON only, with keys: "
        "vehicle (brand such as Toyota, Honda, Nissan, Suzuki, or null), "
        "year (integer or null), "
        "product_type ('spare_part' or 'accessory' or null), "
        "item (product name keyword such as 'brake pad', or null), "
        "max_price (number or null).\n"
        f"Message: {user_text}"
    )
    text = resp.content
    try:
        query = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        query = {}
    return {"customer_need": user_text, "query": query}


def search_info(state: State):
    return {"products": query_database(state.get("query", {}))}


def recommend(state: State):
    products = state.get("products", [])
    if not products:
        text = "Sorry, we could not find a matching product in our stock."
    else:
        resp = get_model().invoke(
            "You are a friendly sales assistant. Recommend the best option(s) from the products below "
            "in 3-4 sentences and ask if the customer is interested.\n"
            f"Customer message: {state['customer_need']}\nProducts: {products}"
        )
        text = resp.content
    return {"recommendation": text}


def _build_find_graph():
    g = StateGraph(State)
    g.add_node("understand_customer", understand_customer)
    g.add_node("search_info", search_info)
    g.add_node("recommend", recommend)
    g.add_edge(START, "understand_customer")
    g.add_edge("understand_customer", "search_info")
    g.add_edge("search_info", "recommend")
    g.add_edge("recommend", END)
    return g.compile()


_find_graph = None


def find_products(customer_message: str) -> dict:
    """Run understand_customer -> search_info -> recommend.

    Returns {"customer_need", "query", "products", "recommendation"}.
    """
    global _find_graph
    if _find_graph is None:
        _find_graph = _build_find_graph()
    out = _find_graph.invoke({"messages": [HumanMessage(content=customer_message)]})
    return {
        "customer_need": out.get("customer_need", customer_message),
        "query": out.get("query", {}),
        "products": out.get("products", []),
        "recommendation": out.get("recommendation", ""),
    }


# ------------------------------------------------ interest + order + send
_YES = ("yes", "yeah", "yep", "sure", "ok", "okay", "ya", "oya", "ow", "buy", "order", "interested", "i want", "i'll take", "take it")
_NO = ("no", "nope", "not interested", "no thanks", "nah", "later", "cancel")


def is_interested(reply: str) -> bool:
    text = reply.strip().lower()
    if any(text == w or text.startswith(w + " ") or text.startswith(w + ",") for w in _NO):
        return False
    if text in _YES or text.rstrip("!. ") in _YES:
        return True
    resp = get_model().invoke(
        "Does the customer want to go ahead with the purchase? Answer only 'yes' or 'no'.\n"
        f"Customer: {reply}"
    )
    return resp.content.strip().lower().startswith("yes")


def build_order_message(customer: dict, products: list, customer_need: str) -> str:
    return (
        "New order!\n"
        f"Customer: {customer['name']}\n"
        f"Phone: {customer['phone']}\n"
        f"Address: {customer['address']}\n"
        f"Request: {customer_need}\n"
        f"Products:\n{format_products(products)}"
    )


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    r.raise_for_status()


def whatsapp_link(text: str):
    """Fallback: WhatsApp click-to-chat link (a person taps Send). None if no number is set."""
    seller = os.getenv("SELLER_WHATSAPP_TO", "").replace("+", "").replace(" ", "")
    return f"https://wa.me/{seller}?text={quote(text)}" if seller else None


def send_order(customer: dict, products: list, customer_need: str) -> dict:
    """Send the confirmed order to the seller.

    Returns {"channel": "telegram" | "whatsapp_link", "message": str, "link": str | None}.
    """
    body = build_order_message(customer, products, customer_need)
    if telegram_configured():
        send_telegram(body)
        return {"channel": "telegram", "message": body, "link": None}
    link = whatsapp_link(body)
    if link:
        return {"channel": "whatsapp_link", "message": body, "link": link}
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (or SELLER_WHATSAPP_TO) in .env")
