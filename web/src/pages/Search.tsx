import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import PlaceInput from "../components/PlaceInput";
import { todayPlus } from "../api";

const MODES = [
  ["flight", "Flights"],
  ["train", "Trains"],
  ["bus", "Buses"],
  ["hotel", "Stays"],
] as const;

export default function SearchPage() {
  const nav = useNavigate();
  const [mode, setMode] = useState<(typeof MODES)[number][0]>("flight");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [date, setDate] = useState(todayPlus(7));
  const [out, setOut] = useState(todayPlus(9));
  const [people, setPeople] = useState(1);
  const hotel = mode === "hotel";

  function go(e: FormEvent) {
    e.preventDefault();
    const q = hotel
      ? { type: "hotel", city: to, check_in: date, check_out: out, guests: String(people) }
      : { type: "transport", kind: mode, origin: from, destination: to, date, adults: String(people) };
    nav(`/results?${new URLSearchParams(q)}`);
  }

  return (
    <form className="panel" onSubmit={go}>
      <h1>Where to?</h1>
      <div className="chips" role="tablist">
        {MODES.map(([v, label]) => (
          <button type="button" key={v} role="tab" aria-selected={mode === v} className={mode === v ? "chip on" : "chip"} onClick={() => setMode(v)}>
            {label}
          </button>
        ))}
      </div>
      <div className="grid">
        {!hotel && <PlaceInput label="From" value={from} onChange={setFrom} placeholder="Delhi" />}
        <PlaceInput label={hotel ? "City" : "To"} value={to} onChange={setTo} placeholder="Goa" />
        <label className="field">
          <span>{hotel ? "Check-in" : "Date"}</span>
          <input type="date" min={todayPlus(0)} value={date} required onChange={(e) => { setDate(e.target.value); if (out <= e.target.value) setOut(e.target.value); }} />
        </label>
        {hotel && (
          <label className="field">
            <span>Check-out</span>
            <input type="date" min={date} value={out} required onChange={(e) => setOut(e.target.value)} />
          </label>
        )}
        <label className="field">
          <span>{hotel ? "Guests" : "Travellers"}</span>
          <input type="number" min={1} max={9} value={people} onChange={(e) => setPeople(Math.min(9, Math.max(1, Number(e.target.value) || 1)))} />
        </label>
      </div>
      <button className="btn">Search</button>
    </form>
  );
}
