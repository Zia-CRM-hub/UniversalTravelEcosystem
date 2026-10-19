#!/usr/bin/env python
"""One-command, no-accounts demo of the whole backend.

    cd backend && .venv/Scripts/python ../scripts/demo.py        (Linux/macOS: .venv/bin/python)

Starts the real API (demo supplier, demo payment gateway, temp SQLite) plus two small local stand-ins:
  * a keyword-driven "LLM" speaking the OpenAI chat-completions + tool-calling protocol, and
  * a fake WhatsApp Graph API that records the messages the bot sends.
Then walks through search -> plan -> one-payment checkout -> cancel -> chat agent -> WhatsApp.
The stand-in LLM only understands a few phrases; a real model (Ollama etc.) is far more flexible.
"""
import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1] / "backend"
APP_SECRET = "demo-app-secret"
WA_SENT: list[dict] = []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- stand-in LLM
class FakeLLM(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        last = msgs[-1]
        if last["role"] == "tool":
            data = json.loads(last["content"])
            text = "I couldn't find anything for that." if data.get("error") or data.get("results") == [] else (
                "Here is a complete trip plan." if "itinerary" in data else "Here are the best options I found.")
            msg = {"role": "assistant", "content": text}
        else:
            msg = self.decide(last["content"].lower())
        out = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    @staticmethod
    def decide(text: str) -> dict:
        m = re.search(r"([a-z]+) to ([a-z]+)", text)
        if not m:
            return {"role": "assistant", "content": "Where would you like to go, and when?"}
        days = int((re.search(r"in (\d+) days", text) or [0, 10])[1])
        day = (date.today() + timedelta(days=days)).isoformat()
        if "plan" in text or "trip" in text:
            n = int((re.search(r"for (\d+)", text) or [0, 2])[1])
            end = (date.today() + timedelta(days=days + 4)).isoformat()
            name, args = "plan_trip", {"origin": m[1], "destination": m[2], "start": day, "end": end,
                                       "travelers": n, "mode": "train"}
        else:
            kind = "train" if "train" in text else "bus" if "bus" in text else "flight"
            name, args = "search_transport", {"kind": kind, "origin": m[1], "destination": m[2], "date": day}
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class FakeGraph(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        WA_SENT.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


def serve(handler, port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- helpers
def money(minor, cur="INR"):
    return f"{minor / 100:,.2f} {cur}"


def step(n, title):
    print(f"\n{'=' * 78}\n STEP {n}: {title}\n{'=' * 78}")


def show(o):
    if o["kind"] == "hotel":
        return f"{o['name']} ({o['stars']}*, {o['rating']}) {o['nights']} nights  {money(o['price'], o['currency'])}"
    return (f"{o['carrier']:<16} {o['number']:<8} {o['departure'][11:16]}->{o['arrival'][11:16]}  "
            f"{'direct' if not o['stops'] else str(o['stops']) + ' stop':<7} {money(o['price'], o['currency'])}")


def main():
    llm_port, wa_port, api_port = free_port(), free_port(), free_port()
    serve(FakeLLM, llm_port)
    serve(FakeGraph, wa_port)
    db = Path(tempfile.mkdtemp()) / "demo.db"
    env = {**os.environ,
           "DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}", "PROVIDERS": "demo", "APP_ENV": "dev",
           "RATE_LIMIT_PER_MINUTE": "0", "LLM_BASE_URL": f"http://127.0.0.1:{llm_port}/v1",
           "WEB_BASE_URL": "https://travel.example.com",
           "WHATSAPP_VERIFY_TOKEN": "verify-me", "WHATSAPP_APP_SECRET": APP_SECRET, "WHATSAPP_ACCESS_TOKEN": "t",
           "WHATSAPP_PHONE_NUMBER_ID": "12345", "WHATSAPP_GRAPH_URL": f"http://127.0.0.1:{wa_port}"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(api_port)],
                              cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{api_port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(base + "/health/live", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            sys.exit("API did not start")
        run(base)
    finally:
        server.terminate()


def run(base):
    c = httpx.Client(base_url=base, timeout=30)
    d1, d2 = [(date.today() + timedelta(days=d)).isoformat() for d in (10, 14)]

    step(1, "The system is up; which suppliers, agent and WhatsApp are enabled?")
    r = c.get("/health/ready").json()
    print(f" status={r['status']}  suppliers={[p['name'] for p in r['providers']]}  agent={r['agent']}  whatsapp={r['whatsapp']}")

    step(2, "Create an account (what the web/mobile sign-up screen does)")
    tok = c.post("/v1/auth/register", json={"email": "asha@example.com", "name": "Asha", "password": "password123"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}
    print(" registered asha@example.com, got a bearer token")

    step(3, "Search flights Delhi -> Goa for 2 travellers (price is the total for the party)")
    s = c.get("/v1/search/transport", params={"kind": "flight", "origin": "Delhi", "destination": "Goa", "date": d1, "adults": 2}).json()
    for o in s["offers"][:4]:
        print("  ", show(o))
    print(f"   ... {len(s['offers'])} offers, cheapest first; warnings={s['warnings']}")
    print(" Each offer has an opaque offer_id; the server remembers the price, the client can't change it.")

    step(4, "One-tap trip plan: outbound + return + best-value hotel within a budget")
    body = {"origin": "Mumbai", "destination": "Goa", "start": d1, "end": d2, "travelers": 2, "mode": "train"}
    plan = c.post("/v1/trips/plan", json=body).json()
    for k in ("outbound", "return", "hotel"):
        print(f"   {k:<9}", show(plan[k]))
    print(f"   TOTAL    {money(plan['total'], plan['currency'])}   itinerary: " + "; ".join(f"D{x['day']} {x['idea']}" for x in plan["itinerary"]))
    over = c.post("/v1/trips/plan", json={**body, "budget": 100})
    print(f" With a 100 INR budget the planner refuses: {over.status_code} {over.json()['detail']}")

    step(5, "Checkout: ONE payment for all three bookings")
    offers = [plan["outbound"], plan["return"], plan["hotel"]]
    co = c.post("/v1/checkouts", headers=auth, json={"offer_ids": [o["offer_id"] for o in offers]}).json()
    print(f" checkout {co['id'][:8]}...  amount={money(co['amount'], co['currency'])}  gateway={co['gateway']}  order={co['gateway_order_id']}")
    print(" (with Razorpay the app now opens the payment sheet; the demo gateway skips it)")
    pax = [{"title": "ms", "gender": "f", "given_name": f"Asha{i}", "family_name": "Rao", "born_on": "1990-05-17"} for i in range(2)]
    confirm = {"payment": None, "passengers": pax, "contact": {"email": "asha@example.com", "phone": "+919876543210"}}
    r = c.post(f"/v1/checkouts/{co['id']}/confirm", headers=auth, json=confirm)
    print(f" confirm -> HTTP {r.status_code}, status={r.json()['status']}")
    for b in r.json()["bookings"]:
        print(f"   {b['reference']}  {b['kind']:<6} {b['status']:<10} {money(b['total_price'], b['currency'])}")
    again = c.post(f"/v1/checkouts/{co['id']}/confirm", headers=auth, json=confirm)
    same = [b["id"] for b in again.json()["bookings"]] == [b["id"] for b in r.json()["bookings"]]
    print(f" Retrying confirm (dropped connection?) -> HTTP {again.status_code}, same bookings returned: {same}, nothing booked twice")

    step(6, "My trips, then cancel just the hotel")
    mine = c.get("/v1/bookings", headers=auth).json()
    hotel = next(b for b in mine if b["kind"] == "hotel")
    print(f" {len(mine)} bookings listed. Cancelling {hotel['reference']} ...")
    cx = c.post(f"/v1/bookings/{hotel['id']}/cancel", headers=auth).json()
    print(f" -> {cx['status']} (the supplier is told; only the hotel's share would be refunded)")
    twice = c.post("/v1/bookings/" + hotel["id"] + "/cancel", headers=auth).json()
    print(" Cancelling again ->", twice["detail"])

    step(7, "Chat agent (same one in the web app, mobile app and WhatsApp)")
    for msg in ["cheapest flight Delhi to Goa in 12 days", "plan a trip Mumbai to Goa for 2 in 20 days"]:
        r = c.post("/v1/agent/chat", headers=auth, json={"message": msg}).json()
        print(f' you:   "{msg}"\n bot:   {r["reply"]}')
        for card in r["cards"][:3]:
            if card["type"] == "offer":
                print(f"        option {card['n']}: {show(card['offer'])}\n                  book -> {card['link']}")
            else:
                print(f"        full trip {money(card['total'], card['currency'])}  book all -> {card['link'][:70]}...")
    print(" The bot can search and plan, but it never books or takes payment: it hands off with a link.")

    step(8, "WhatsApp: Meta calls our webhook with a signed message")
    hs = c.get("/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1234"})
    print(f" Webhook handshake -> {hs.text}")
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.demo1", "from": "919999999999", "type": "text", "text": {"body": "cheapest train Delhi to Jaipur in 7 days"}}]}}]}]}
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    forged = c.post("/v1/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
    print(f" Forged request (bad signature) -> HTTP {forged.status_code}, ignored")
    ok = c.post("/v1/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    time.sleep(1.0)
    print(f" Signed request -> HTTP {ok.status_code} immediately; the reply is sent in the background:")
    for m in WA_SENT:
        print(f" --- WhatsApp message to +{m['to']} ---")
        print("\n".join("   " + line for line in m["text"]["body"].splitlines()[:9]))
    c.post("/v1/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    time.sleep(0.5)
    print(f" Meta redelivers the same message -> still only {len(WA_SENT)} reply sent (duplicates ignored)")

    step(9, "Operations view")
    metrics = c.get("/metrics").text
    reqs = [l for l in metrics.splitlines() if l.startswith("bookings_total{")]
    print(" bookings_total metric:", *reqs or ["(none)"], sep="\n   ")
    print(" Every response carries X-Request-ID, and logs are JSON, so one request can be traced end to end.")
    print("\nDemo complete. The data lived in a temp database and is discarded.")


if __name__ == "__main__":
    main()
