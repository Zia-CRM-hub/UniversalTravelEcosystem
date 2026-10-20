import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, Mode, Offer, Warning } from "../api";
import OfferCard from "../components/OfferCard";

export default function ResultsPage() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const [offers, setOffers] = useState<Offer[] | null>(null);
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    setOffers(null);
    setErr("");
    const req =
      sp.get("type") === "hotel"
        ? api.hotels(sp.get("city")!, sp.get("check_in")!, sp.get("check_out")!, Number(sp.get("guests") ?? 1))
        : api.transport(sp.get("kind") as Mode, sp.get("origin")!, sp.get("destination")!, sp.get("date")!, Number(sp.get("adults") ?? 1));
    req.then((r) => { setOffers(r.offers); setWarnings(r.warnings); }).catch((e) => setErr(e.message));
  }, [sp]);

  if (err) return <p className="err panel" role="alert">{err}</p>;
  if (!offers) return <p className="panel mute">Searching…</p>;
  return (
    <section className="panel">
      <h1>Results</h1>
      {warnings.length > 0 && (
        <p className="mute">Some suppliers didn’t respond ({warnings.map((w) => w.provider).join(", ")}), so results may be incomplete.</p>
      )}
      {offers.length === 0 && <p className="mute">No results. Try other dates or places.</p>}
      {offers.map((o, i) => (
        <div key={o.offer_id} className="pick" onClick={() => nav(`/book?offers=${o.offer_id}`)}>
          {i === 0 && offers.length > 1 && <span className="badge">Cheapest</span>}
          <OfferCard o={o} action={<Link className="btn small" to={`/book?offers=${o.offer_id}`} onClick={(e) => e.stopPropagation()}>Book this</Link>} />
        </div>
      ))}
    </section>
  );
}
