import React, { useEffect, useRef, useState } from "react";
import { Platform, Pressable, Text, View } from "react-native";
import DateTimePicker from "@react-native-community/datetimepicker";
import { api, Place } from "./api";
import { color, Field, s } from "./ui";

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parse = (v: string) => {
  const [y, m, d] = v.split("-").map(Number);
  return new Date(y, m - 1, d);
};

/** Tap-to-pick date. Value is a YYYY-MM-DD string. */
export function DateField({ label, value, onChange, min, max, past }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  min?: string;
  max?: string;
  /** Allow dates before today (e.g. date of birth). */
  past?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <View style={{ marginBottom: 12 }}>
      <Text style={s.label}>{label}</Text>
      <Pressable accessibilityRole="button" onPress={() => setOpen(true)} style={s.input}>
        <Text style={{ fontSize: 16, color: color.ink }}>{value}</Text>
      </Pressable>
      {open && (
        <DateTimePicker
          value={parse(value)}
          mode="date"
          minimumDate={min ? parse(min) : past ? undefined : new Date()}
          maximumDate={max ? parse(max) : undefined}
          display={Platform.OS === "ios" ? "inline" : "default"}
          onChange={(e, d) => {
            setOpen(Platform.OS === "ios"); // Android closes itself
            if (e.type === "set" && d) onChange(iso(d));
          }}
        />
      )}
    </View>
  );
}

/** City field with OpenStreetMap suggestions via the backend. */
export function PlaceField({ label, value, onChange, placeholder }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const [options, setOptions] = useState<Place[]>([]);
  const picked = useRef(false);
  const seq = useRef(0);

  useEffect(() => {
    if (picked.current) {
      picked.current = false;
      return;
    }
    if (value.trim().length < 3) return setOptions([]);
    const id = ++seq.current;
    // Debounce 400ms; also keeps us within Nominatim's 1 req/sec policy.
    const t = setTimeout(() => {
      api.places(value.trim())
        .then((r) => id === seq.current && setOptions(r))
        .catch(() => id === seq.current && setOptions([]));
    }, 400);
    return () => clearTimeout(t);
  }, [value]);

  return (
    <View>
      <Field label={label} value={value} onChangeText={onChange} placeholder={placeholder} autoCapitalize="words" />
      {options.length > 0 && (
        <View style={{ marginTop: -8, marginBottom: 12, backgroundColor: "#fff", borderRadius: 10, borderWidth: 1, borderColor: color.line }}>
          {options.map((p, i) => (
            <Pressable
              key={`${p.lat},${p.lon},${i}`}
              onPress={() => {
                picked.current = true;
                onChange(p.name.split(",")[0].trim());
                setOptions([]);
              }}
              style={{ padding: 10, borderTopWidth: i ? 1 : 0, borderColor: color.line }}
            >
              <Text numberOfLines={1}>{p.name}</Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}
