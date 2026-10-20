import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api, Mode, money, Plan, remember, todayPlus } from "../api";
import OfferCard from "../components/OfferCard";
import PlaceInput from "../components/PlaceInput";

export default function PlanPage() {
  const last = remember<{ from: string; to: string }>("last_search");
  const [from, setFrom] = useState(last?.from ?? "");
  const [to, setTo] = useState(last?.to ?? "");
  const [start, setStart] = useState(todayPlus(14));
  const [end, setEnd] = useState(todayPlus(18));
  const [travelers, setTravelers] = useState(2);
  const [mode, setMode] = useState<Mode>("flight");
  const [budget, setBudget] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function go(e: FormEvent) {
    e.preventDefault();
    remember("last_search", { mode, from, to });
    setErr("");
    setPlan(null);
    setBusy(true);
    try {
      setPlan(await api.plan({ origin: from, destination: to, start, end, travelers, mode, budget: budget ? Number(budget) : undefined }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <form onSubmit={go}>
        <h1>Plan my trip</h1>
        <div className="chips">
          {(["flight", "train", "bus"] as Mode[]).map((m) => (
            <button type="button" key={m} className={mode === m ? "chip on" : "chip"} onClick={() => setMode(m)}>{m}</button>
          ))}
        </div>
        <div className="grid">
          <PlaceInput label="From" value={from} onChange={setFrom} placeholder="Delhi" required />
          <PlaceInput label="To" value={to} onChange={setTo} placeholder="Goa" required />
          <label className="field"><span>Start</span><input type="date" min={todayPlus(0)} value={start} required onChange={(e) => { setStart(e.target.value); if (end <= e.target.value) setEnd(e.target.value); }} /></label>
          <label className="field"><span>End</span><input type="date" min={start} value={end} required onChange={(e) => setEnd(e.target.value)} /></label>
          <label className="field"><span>Travellers</span><input type="number" min={1} max={9} value={travelers} onChange={(e) => setTravelers(Math.min(9, Math.max(1, Number(e.target.value) || 1)))} /></label>
          <label className="field"><span>Total budget (optional)</span><input type="number" min={1} value={budget} onChange={(e) => setBudget(e.target.value)} /></label>
        </div>
        {err && <p className="err" role="alert">{err}</p>}
        <button className="btn" disabled={busy}>{busy ? "Planning…" : "Build my plan"}</button>
      </form>
      {plan && (
        <div>
          <h2>Total {money(plan.total, plan.currency)}</h2>
          <OfferCard o={plan.outbound} />
          <OfferCard o={plan.return} />
          <OfferCard o={plan.hotel} />
          <div className="card">{plan.itinerary.map((d) => <div key={d.day}>Day {d.day}: {d.idea}</div>)}</div>
          <Link className="btn" to={`/book?offers=${[plan.outbound, plan.return, plan.hotel].map((o) => o.offer_id).join(",")}`}>Book everything · {money(plan.total, plan.currency)}</Link>
        </div>
      )}
    </section>
  );
}
