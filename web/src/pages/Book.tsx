import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, Booking, Checkout, Contact, money, Offer, Passenger, RazorpayResult } from "../api";
import OfferCard from "../components/OfferCard";
import { payWithRazorpay } from "../razorpay";

const blank = (): Passenger => ({ title: "mr", gender: "m", given_name: "", family_name: "", born_on: "" });

/** Books one or more offers (a single result, or a whole planned trip) for the same travellers, with ONE payment. */
export default function BookPage() {
  const ids = (useSearchParams()[0].get("offers") ?? "").split(",").filter(Boolean);
  const [offers, setOffers] = useState<Offer[] | null>(null);
  const [pax, setPax] = useState<Passenger[]>([]);
  const [contact, setContact] = useState<Contact>({ email: "", phone: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Booking[]>([]);
  // After a network failure we retry the SAME checkout and reuse the payment the customer already made,
  // so they are never asked to pay twice. After a definitive server answer we start fresh.
  const checkout = useRef<Checkout | null>(null);
  const paid = useRef<RazorpayResult | null>(null);

  useEffect(() => {
    Promise.all(ids.map(api.offer)).then((o) => {
      setOffers(o);
      setPax(Array.from({ length: o[0]?.pax ?? 1 }, blank));
    }).catch((e) => setErr(e.message));
  }, []);

  const set = (i: number, k: keyof Passenger, v: string) => setPax(pax.map((p, j) => (j === i ? { ...p, [k]: v } : p)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!offers) return;
    setErr("");
    setBusy(true);
    try {
      const co = (checkout.current ??= await api.checkout(ids));
      if (co.gateway === "razorpay" && !paid.current) {
        paid.current = await payWithRazorpay(co, { email: contact.email, contact: contact.phone });
        if (!paid.current) return; // customer closed the payment window
      }
      setDone((await api.confirm(co.id, paid.current, pax, contact)).bookings);
    } catch (e) {
      if (e instanceof ApiError) {
        checkout.current = null;
        paid.current = null;
        setErr(e.message + (e.status === 402 || e.code === "checkout_failed" ? "" : " If you paid, the hold is released automatically and nothing is charged."));
      } else {
        setErr((e as Error).message || "Network problem. Press the button again to retry safely.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (!ids.length) return <p className="panel err">No offer selected.</p>;
  if (done.length) {
    return (
      <section className="panel">
        <h1>Booked 🎉</h1>
        {done.map((b) => <p key={b.id}>{b.reference} · {b.kind} · {money(b.total_price, b.currency)}</p>)}
        <Link className="btn" to="/trips">View my trips</Link>
      </section>
    );
  }
  if (!offers) return err ? <p className="panel err" role="alert">{err} <Link to="/">Search again</Link></p> : <p className="panel mute">Loading…</p>;

  const total = offers.reduce((s, o) => s + o.price, 0);
  return (
    <form className="panel" onSubmit={submit}>
      <h1>Confirm booking</h1>
      {offers.map((o) => <OfferCard key={o.offer_id} o={o} />)}
      <h2>{pax.length > 1 ? "Travellers" : "Traveller"}</h2>
      <p className="mute">Enter names exactly as on the travel document.</p>
      {pax.map((p, i) => (
        <fieldset key={i}>
          <legend>Traveller {i + 1}</legend>
          <div className="grid">
            <label className="field"><span>Title</span>
              <select value={p.title} onChange={(e) => set(i, "title", e.target.value)}>
                {["mr", "ms", "mrs", "miss", "mx"].map((t) => <option key={t}>{t}</option>)}
              </select></label>
            <label className="field"><span>Gender</span>
              <select value={p.gender} onChange={(e) => set(i, "gender", e.target.value)}>
                <option value="m">Male</option><option value="f">Female</option>
              </select></label>
            <label className="field"><span>Given name</span><input required value={p.given_name} onChange={(e) => set(i, "given_name", e.target.value)} /></label>
            <label className="field"><span>Family name</span><input required value={p.family_name} onChange={(e) => set(i, "family_name", e.target.value)} /></label>
            <label className="field"><span>Date of birth</span><input type="date" required max={new Date().toISOString().slice(0, 10)} value={p.born_on} onChange={(e) => set(i, "born_on", e.target.value)} /></label>
          </div>
        </fieldset>
      ))}
      <h2>Contact</h2>
      <div className="grid">
        <label className="field"><span>Email</span><input type="email" required value={contact.email} onChange={(e) => setContact({ ...contact, email: e.target.value })} /></label>
        <label className="field"><span>Mobile (with country code)</span><input required pattern="\+[1-9][0-9]{6,14}" placeholder="+919876543210" value={contact.phone} onChange={(e) => setContact({ ...contact, phone: e.target.value })} /></label>
      </div>
      {err && <p className="err" role="alert">{err}</p>}
      <button className="btn" disabled={busy}>{busy ? "Processing…" : `Pay ${money(total, offers[0].currency)}`}</button>
      <p className="mute">You are charged only after every booking is confirmed.</p>
    </form>
  );
}
