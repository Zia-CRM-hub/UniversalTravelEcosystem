import { useEffect, useRef, useState } from "react";
import { api } from "../api";

// Shown instantly (no network) so the first keystrokes already suggest something useful.
const POPULAR = [
  "Delhi", "Mumbai", "Bengaluru", "Chennai", "Kolkata", "Hyderabad", "Pune", "Ahmedabad", "Goa", "Jaipur", "Kochi",
  "Lucknow", "Chandigarh", "Udaipur", "Varanasi", "Amritsar", "Shimla", "Manali", "Srinagar", "Rishikesh", "Agra",
  "Mysuru", "Thiruvananthapuram", "Visakhapatnam", "Indore", "Bhopal", "Nagpur", "Patna", "Guwahati", "Darjeeling",
  "Dubai", "Singapore", "London", "Paris", "New York", "Bangkok", "Kuala Lumpur", "Tokyo", "Sydney", "Colombo",
  "Kathmandu", "Male", "Bali", "Istanbul", "Rome", "Barcelona", "Amsterdam", "Toronto", "San Francisco", "Hong Kong",
];

/** City input with OpenStreetMap suggestions (via the backend), using a native <datalist>. */
export default function PlaceInput({ label, value, onChange, placeholder, required }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
}) {
  const [remote, setRemote] = useState<string[]>([]);
  const seq = useRef(0);
  const id = useRef(`places-${Math.random().toString(36).slice(2)}`).current;

  useEffect(() => {
    if (value.trim().length < 3) return setRemote([]);
    const n = ++seq.current;
    // Debounce; also keeps us inside Nominatim's 1 request/second policy.
    const t = setTimeout(() => {
      api.places(value.trim())
        .then((r) => n === seq.current && setRemote([...new Set(r.map((p) => p.name.split(",")[0].trim()))]))
        .catch(() => n === seq.current && setRemote([]));
    }, 400);
    return () => clearTimeout(t);
  }, [value]);

  const q = value.trim().toLowerCase();
  const local = q ? POPULAR.filter((c) => c.toLowerCase().startsWith(q)) : [];
  const options = [...new Set([...local, ...remote])].slice(0, 10);

  return (
    <label className="field">
      <span>{label}</span>
      <input list={id} value={value} placeholder={placeholder} required={required} onChange={(e) => onChange(e.target.value)} autoComplete="off" />
      <datalist id={id}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </label>
  );
}
