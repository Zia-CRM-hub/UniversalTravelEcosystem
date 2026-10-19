import { useEffect, useState } from "react";
import { api, Booking, money } from "../api";

export default function TripsPage() {
  const [items, setItems] = useState<Booking[] | null>(null);
  const [err, setErr] = useState("");
  const load = () => api.bookings().then(setItems).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function cancel(b: Booking) {
    if (!confirm(`Cancel booking ${b.reference}?`)) return;
    try { await api.cancel(b.id); await load(); } catch (e) { setErr((e as Error).message); }
  }

  return (
    <section className="panel">
      <h1>My trips</h1>
      {err && <p className="err" role="alert">{err}</p>}
      {items?.length === 0 && <p className="mute">No bookings yet.</p>}
      {items?.map((b) => (
        <div className="card" key={b.id}>
          <div className="row">
            <strong>{b.offer.kind === "hotel" ? b.offer.name : `${b.offer.origin} → ${b.offer.destination}`}</strong>
            <span className={b.status === "confirmed" ? "ok" : "bad"}>{b.status}</span>
          </div>
          <div className="mute">{b.reference} · {b.kind}{b.failure_reason ? ` · ${b.failure_reason}` : ""}</div>
          <div className="row"><span className="price">{money(b.total_price, b.currency)}</span>
            {b.status === "confirmed" && <button className="btn small ghost" onClick={() => cancel(b)}>Cancel</button>}
          </div>
        </div>
      ))}
    </section>
  );
}
