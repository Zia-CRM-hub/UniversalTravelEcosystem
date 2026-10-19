import type { ReactNode } from "react";
import { clock, hm, money, Offer } from "../api";

export default function OfferCard({ o, action }: { o: Offer; action?: ReactNode }) {
  if (o.kind === "hotel") {
    return (
      <div className="card">
        <div className="row">
          <strong>{o.name}</strong>
          <span className="ok">{o.rating} ★</span>
        </div>
        <div className="mute">
          {"★".repeat(o.stars ?? 0)} · {o.amenities?.join(" · ")}
        </div>
        <div className="row">
          <span className="mute">
            {money(o.price_per_night ?? 0, o.currency)}/night × {o.nights}
          </span>
          <span className="price">{money(o.price, o.currency)}</span>
        </div>
        {action}
      </div>
    );
  }
  return (
    <div className="card">
      <div className="row">
        <strong>
          {o.carrier} · {o.number}
        </strong>
        <span className="price">{money(o.price, o.currency)}</span>
      </div>
      <div className="row">
        <span className="time">{clock(o.departure!)}</span>
        <span className="mute">
          {hm(o.duration_min ?? 0)} · {o.stops ? `${o.stops} stop` : "direct"}
        </span>
        <span className="time">{clock(o.arrival!)}</span>
      </div>
      <div className="mute">
        {o.origin} → {o.destination}
        {o.pax > 1 && ` · ${o.pax} travellers`}
      </div>
      {action}
    </div>
  );
}
