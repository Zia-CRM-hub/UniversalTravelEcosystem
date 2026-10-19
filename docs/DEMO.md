# Demo walkthrough

Three ways to see the product, from "works right now with nothing installed but Python" to "live with real suppliers". Be clear with your audience about which one they are seeing.

| | What you see | Needs |
|---|---|---|
| **A. Scripted demo** | The whole backend: search, plan, one-payment checkout, cancel, chat agent, WhatsApp | Python 3.12+ only. **Verified; the transcript below is real output.** |
| **B. Apps demo** | The web and mobile screens on the demo supplier | Node 20. *Not yet run by the author: expect small fixes first.* |
| **C. Live-services demo** | Real Duffel sandbox flights, Razorpay test payments, a real WhatsApp number | Accounts and keys. *Untested against the real services.* |

> In A and B the flights, trains, buses and hotels are **synthetic** and no money moves. Say so.

---

## A. Scripted demo (about 20 seconds)

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python ../scripts/demo.py
```

The script starts the real API on a temporary database, plus two small stand-ins on local ports: a keyword-driven "LLM" speaking the OpenAI tool-calling protocol, and a fake WhatsApp server that records what the bot sends. The stand-in LLM understands only phrases like "cheapest flight Delhi to Goa in 12 days"; a real model (Ollama and others) is far more flexible.

### What happens, step by step (real output, lightly trimmed)

**1. The system is up.** Which suppliers, agent and WhatsApp are on.
```
status=ok  suppliers=['demo']  agent=True  whatsapp=True
```

**2. Create an account.** Same as the sign-up screen.

**3. Search flights Delhi → Goa for 2 travellers.** Cheapest first; the price is the total for the party. Each result carries an opaque `offer_id`, so the client cannot alter the price.
```
AeroBlue         AE3252   06:36->08:46  direct  4,660.00 INR
Horizon Air      HO3980   18:49->20:59  1 stop  6,060.00 INR
Horizon Air      HO3621   11:58->14:08  direct  6,120.00 INR
Nimbus           NI2635   11:41->13:51  direct  6,920.00 INR
... 5 offers, cheapest first; warnings=[]
```
`warnings` is where a slow or broken supplier would show up. The search still returns everyone else's results.

**4. One-tap trip plan.** Outbound, return and best-value hotel, with a day-by-day outline. Budget is enforced:
```
outbound  Express Line     EX2943   19:30->08:13  direct  2,820.00 INR
return    Express Line     EX4083   13:39->02:22  direct  3,480.00 INR
hotel     Central Grand Goa (3*, 4.0) 4 nights  27,560.00 INR
TOTAL    33,860.00 INR   itinerary: D1 Old town & landmarks walk; D2 Local food tour; D3 Museum…
With a 100 INR budget the planner refuses: 422 No combination fits the budget; the cheapest needs 33860 INR
```

**5. Checkout: one payment, three bookings.**
```
checkout c6b7e857...  amount=33,860.00 INR  gateway=demo  order=demo_order_8e92de1f1891
confirm -> HTTP 200, status=booked
  UT6775438D  train  confirmed  2,820.00 INR
  UT2F6BEABB  train  confirmed  3,480.00 INR
  UT82A835B4  hotel  confirmed  27,560.00 INR
Retrying confirm (dropped connection?) -> HTTP 200, same bookings returned: True, nothing booked twice
```
With Razorpay, the app opens the payment sheet between "checkout" and "confirm". **Talking point:** money is *held* when the customer pays and only *captured* after all three suppliers confirm. If the hotel failed, the train bookings would be cancelled and the hold released (this is covered by automated tests: `tests/test_checkout.py`).

**6. My trips, and cancel just the hotel.**
```
3 bookings listed. Cancelling UT82A835B4 ...
-> cancelled (the supplier is told; only the hotel's share would be refunded)
Cancelling again -> A cancelled booking cannot be cancelled
```

**7. The chat agent**, the same one used in the web app, mobile app and WhatsApp:
```
you:   "cheapest flight Delhi to Goa in 12 days"
bot:   Here are the best options I found.
       option 1: Nimbus  NI9746  20:01->22:11  direct  2,830.00 INR
                 book -> https://travel.example.com/book?offers=0f852e85…
       option 2: …
you:   "plan a trip Mumbai to Goa for 2 in 20 days"
bot:   Here is a complete trip plan.
       full trip 27,400.00 INR  book all -> https://travel.example.com/book?offers=9408c357…
```
**Talking point:** the bot can search and plan but **cannot book or take payment**. It hands off with a link. Passenger details and payment do not belong in a chat transcript, and a read-only agent limits what a manipulated model could do. Prices and links are produced by code from real search results, not typed by the model.

**8. WhatsApp.** Meta calls the webhook with a signed message.
```
Webhook handshake -> 1234
Forged request (bad signature) -> HTTP 403, ignored
Signed request -> HTTP 200 immediately; the reply is sent in the background:
--- WhatsApp message to +919999999999 ---
  Here are the best options I found.

  1) Express Line EX8585 delhi->jaipur 15:08-23:39, direct, 1,170.00 INR total
  Book: https://travel.example.com/book?offers=0b8c355c…
  2) …
Meta redelivers the same message -> still only 1 reply sent (duplicates ignored)
```

**9. Operations view.** `bookings_total{outcome="confirmed",provider="demo"} 3.0`. Every response has an `X-Request-ID` and logs are JSON, so a request can be traced end to end.

### Optional: poke the API yourself

Run the API (`uvicorn app.main:app`), open <http://localhost:8000/docs> and try the endpoints. Register, authorize with the token, call `/v1/search/transport`, then `/v1/checkouts` and `/v1/checkouts/{id}/confirm`.

---

## B. Apps demo (web and mobile)

Terminal 1: `cd backend && uvicorn app.main:app --reload`
(Optional, for the Assistant tab: run Ollama and set `LLM_BASE_URL=http://localhost:11434/v1`.)

**Web:** `cd web && npm install && npm run dev`, open <http://localhost:5173>.
**Mobile:** `cd mobile && npm install && npm start`, press `a` for an Android emulator. Set `extra.apiUrl` in `mobile/app.json` first.

Suggested 5-minute path:

1. **Sign up** (Log in → "New here? Sign up").
2. **Search**: Flights, Delhi → Goa, a week out. Cities autocomplete from OpenStreetMap as you type. Pick a result.
3. **Book**: enter traveller details and contact, press **Pay**. The demo gateway confirms immediately and you see a reference.
4. **My trips**: the booking is listed; cancel it.
5. **Plan**: Mumbai → Goa, train, 2 travellers, optional budget. Review outbound, return, hotel and itinerary, then **Book everything**: one payment, three bookings.
6. **Assistant**: "cheapest train Delhi to Jaipur next week". Tap a card to book, which opens the same booking screen.
7. **Show the hand-off**: copy a Book link from a chat card and open it in a private window. It asks you to log in, then lands on the same booking page.

If something does not work on first run, that is expected (these apps have not been built by the author yet). `npm run typecheck` will point at it.

---

## C. Live-services demo (checklist)

Do this in order; stop at the first failure and fix it before continuing.

**Duffel sandbox** (real API shape, test airline inventory)
- [ ] `.env`: `PROVIDERS=duffel`, `DUFFEL_TOKEN=duffel_test_…`
- [ ] Search a real route by IATA code (e.g. `LHR` → `JFK`) and by city name (exercises place resolution).
- [ ] Book a result with `PAYMENT_GATEWAY=demo` first. Confirm the order appears in the Duffel dashboard.
- [ ] Cancel it and confirm the cancellation appears. *(The two-step cancel is from memory; this is where it gets verified.)*

**Razorpay test mode**
- [ ] Test keys in `.env`, `PAYMENT_GATEWAY=razorpay`, **Manual Capture enabled** in the dashboard.
- [ ] Pay with a Razorpay test card (see their docs for test card numbers). The payment shows as **Authorized**, then **Captured** after booking.
- [ ] Force a supplier failure (e.g. a bad passenger name) and confirm the payment is **not** captured and releases.

**WhatsApp**
- [ ] Meta test number, webhook pointed at your public URL (use a tunnel such as ngrok for a laptop), verify token matches.
- [ ] Send "cheapest flight Delhi to Goa next Friday" and check the reply and Book link.

**Both suppliers together**: `PROVIDERS=duffel,amadeus` only if you hold an Amadeus Enterprise contract. Set `AMADEUS_CURRENCY` to match Duffel so prices compare.

---

## What to say about limits

Short, honest version for any audience:

- The architecture, booking logic, agent and WhatsApp handling are built and tested end to end **on a simulated supplier**.
- Real suppliers, Razorpay and WhatsApp are **built to their documented APIs but not yet run live**: that is the next milestone, and the checklist above is how.
- Hotels, trains and buses need real suppliers added; today only flights can be real (Duffel).
- Before real customers: reconciliation job, encrypted passenger data, email verification and password reset, and a security review (see [OPERATIONS.md](OPERATIONS.md)).
