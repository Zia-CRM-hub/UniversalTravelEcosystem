import { useEffect, useRef, useState } from "react";
import { api } from "../api";

/** City input with OpenStreetMap suggestions (via the backend), using a native <datalist>. */
export default function PlaceInput({ label, value, onChange, placeholder, required }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
}) {
  const [options, setOptions] = useState<string[]>([]);
  const seq = useRef(0);
  const id = useRef(`places-${Math.random().toString(36).slice(2)}`).current;

  useEffect(() => {
    if (value.trim().length < 3) return setOptions([]);
    const n = ++seq.current;
    // Debounce; also keeps us inside Nominatim's 1 request/second policy.
    const t = setTimeout(() => {
      api.places(value.trim())
        .then((r) => n === seq.current && setOptions([...new Set(r.map((p) => p.name.split(",")[0].trim()))]))
        .catch(() => n === seq.current && setOptions([]));
    }, 400);
    return () => clearTimeout(t);
  }, [value]);

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
