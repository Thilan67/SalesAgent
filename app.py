"""Streamlit frontend. Run with:  streamlit run app.py

All logic lives in backend.py; this file only draws the chat and keeps track of the step.
Steps (st.session_state.stage):
  ask      -> waiting for the customer's request
  interest -> products were recommended, waiting for "yes / no"
  details  -> customer fills name / phone / address and picks products
  confirm  -> customer checks the summary and confirms
"""
import re

import streamlit as st

import backend

st.set_page_config(page_title="Auto Parts Sales Assistant", page_icon="🚗")
st.title("🚗 Auto Parts Sales Assistant")
st.caption("Tell me which vehicle and part or accessory you need.")


def init_state():
    if "stage" not in st.session_state:
        st.session_state.stage = "ask"
        st.session_state.messages = [
            {"role": "assistant", "content": "Hi! What are you looking for? "}
        ]
        st.session_state.found = {}      # result of backend.find_products
        st.session_state.order = {}      # customer + chosen products


def say(role: str, content: str):
    st.session_state.messages.append({"role": role, "content": content})


def reset_flow(message: str | None = None):
    st.session_state.stage = "ask"
    st.session_state.found = {}
    st.session_state.order = {}
    if message:
        say("assistant", message)


init_state()

with st.sidebar:
    st.subheader("Options")
    if st.button("Start over"):
        for key in ("stage", "messages", "found", "order"):
            st.session_state.pop(key, None)
        st.rerun()
    st.caption(
        "Seller alerts: Telegram" if backend.telegram_configured() else "Seller alerts: WhatsApp link (Telegram not set in .env)"
    )

# ------------------------------------------------------------- chat history
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

stage = st.session_state.stage

# --------------------------------------------------- stage-specific widgets
if stage == "details":
    products = st.session_state.found["products"]
    labels = {p["id"]: f"{p['name']} - {p['vehicle']} {p['year']} - {p['price']}" for p in products}
    with st.form("details_form"):
        st.markdown("**Your details**")
        chosen_ids = st.multiselect(
            "Products to order",
            options=list(labels),
            default=[products[0]["id"]],
            format_func=lambda i: labels[i],
        )
        name = st.text_input("Full name")
        phone = st.text_input("Phone number")
        address = st.text_area("Delivery address")
        submitted = st.form_submit_button("Review order")
    if submitted:
        errors = []
        if not chosen_ids:
            errors.append("Pick at least one product.")
        if not name.strip():
            errors.append("Enter your name.")
        if len(re.sub(r"\D", "", phone)) < 7:
            errors.append("Enter a valid phone number.")
        if not address.strip():
            errors.append("Enter your delivery address.")
        if errors:
            for e in errors:
                st.error(e)
        else:
            st.session_state.order = {
                "customer": {"name": name.strip(), "phone": phone.strip(), "address": address.strip()},
                "products": [p for p in products if p["id"] in chosen_ids],
            }
            st.session_state.stage = "confirm"
            st.rerun()

elif stage == "confirm":
    order = st.session_state.order
    c = order["customer"]
    st.markdown("**Please confirm your order**")
    st.text(f"Name:    {c['name']}\nPhone:   {c['phone']}\nAddress: {c['address']}")
    st.text(backend.format_products(order["products"]))
    col1, col2 = st.columns(2)
    if col1.button("✅ Confirm order", type="primary"):
        try:
            result = backend.send_order(c, order["products"], st.session_state.found["customer_need"])
        except Exception as e:  # network, bad token, nothing configured ...
            st.error(f"Could not send the order to the seller: {e}")
        else:
            reply = "Thank you! Your order was sent to the seller, who will contact you soon."
            if result["channel"] == "whatsapp_link":
                reply += f"\n\nTelegram is not set up, so [open this WhatsApp link and tap Send]({result['link']})."
            say("assistant", reply)
            reset_flow("Anything else I can help you find?")
            st.rerun()
    if col2.button("✖ Cancel"):
        reset_flow("Order cancelled. Nothing was sent. What else can I help you find?")
        st.rerun()

# --------------------------------------------------------------- chat input
prompt = st.chat_input(
    "Type here...",
    disabled=stage in ("details", "confirm"),
)

if prompt:
    say("user", prompt)
    try:
        if stage == "ask":
            with st.spinner("Searching..."):
                found = backend.find_products(prompt)
            say("assistant", found["recommendation"])
            if found["products"]:
                st.session_state.found = found
                st.session_state.stage = "interest"
        elif stage == "interest":
            if backend.is_interested(prompt):
                st.session_state.stage = "details"
                say("assistant", "Great! Please fill in your details below.")
            else:
                reset_flow("No problem! Let me know if you need anything else.")
    except Exception as e:
        say("assistant", f"Sorry, something went wrong: {e}")
    st.rerun()
