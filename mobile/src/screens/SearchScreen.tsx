import React, { useState } from "react";
import { ScrollView, Text } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import type { Mode } from "../api";
import { DateField, PlaceField } from "../inputs";
import { Button, Chips, ErrorText, Field, s } from "../ui";

export type Stack = {
  Tabs: undefined;
  Results:
    | { type: "transport"; mode: Mode; origin: string; destination: string; date: string; adults: number }
    | { type: "hotel"; city: string; checkIn: string; checkOut: string; guests: number };
  /** One offer, or the three offers of a planned trip, booked for the same travellers. */
  Book: { offerIds: string[] };
};

export const plusDaysFrom = (iso: string, n: number) => {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(y, m - 1, d + n);
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
};
export const plusDays = (n: number) => {
  const t = new Date();
  return plusDaysFrom(`${t.getFullYear()}-${t.getMonth() + 1}-${t.getDate()}`, n);
};

export default function SearchScreen({ navigation }: NativeStackScreenProps<Stack, "Tabs">) {
  const [mode, setMode] = useState<Mode | "hotel">("flight");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [date, setDate] = useState(plusDays(7));
  const [out, setOut] = useState(plusDays(9));
  const [people, setPeople] = useState("1");
  const [err, setErr] = useState("");

  function go() {
    const n = Math.min(9, Math.max(1, Number(people) || 1));
    if (mode === "hotel") {
      if (to.trim().length < 2) return setErr("Enter a city");
      navigation.navigate("Results", { type: "hotel", city: to.trim(), checkIn: date, checkOut: out, guests: n });
    } else {
      if (from.trim().length < 2 || to.trim().length < 2) return setErr("Enter both cities");
      navigation.navigate("Results", { type: "transport", mode, origin: from.trim(), destination: to.trim(), date, adults: n });
    }
    setErr("");
  }

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.pad} keyboardShouldPersistTaps="handled">
      <Text style={s.h1}>Where to?</Text>
      <Chips
        value={mode}
        onChange={setMode}
        options={[
          { value: "flight", label: "Flights" },
          { value: "train", label: "Trains" },
          { value: "bus", label: "Buses" },
          { value: "hotel", label: "Stays" },
        ]}
      />
      {mode !== "hotel" && <PlaceField label="From" value={from} onChange={setFrom} placeholder="Delhi" />}
      <PlaceField label={mode === "hotel" ? "City" : "To"} value={to} onChange={setTo} placeholder="Goa" />
      <DateField
        label={mode === "hotel" ? "Check-in" : "Date"}
        value={date}
        onChange={(v) => {
          setDate(v);
          if (out <= v) setOut(plusDaysFrom(v, 1));
        }}
      />
      {mode === "hotel" && <DateField label="Check-out" value={out} onChange={setOut} min={plusDaysFrom(date, 1)} />}
      <Field label={mode === "hotel" ? "Guests" : "Travellers"} value={people} onChangeText={setPeople} keyboardType="number-pad" />
      <ErrorText msg={err} />
      <Button title="Search" onPress={go} />
    </ScrollView>
  );
}
