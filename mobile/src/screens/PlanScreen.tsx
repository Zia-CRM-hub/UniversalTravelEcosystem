import React, { useState } from "react";
import { ScrollView, Text, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { api, Mode, money, Plan } from "../api";
import { DateField, PlaceField } from "../inputs";
import { Button, Card, Chips, ErrorText, Field, s } from "../ui";
import { OfferCard } from "./ResultsScreen";
import { plusDays, plusDaysFrom, Stack } from "./SearchScreen";

export default function PlanScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<Stack>>();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [start, setStart] = useState(plusDays(14));
  const [end, setEnd] = useState(plusDays(18));
  const [people, setPeople] = useState("2");
  const [budget, setBudget] = useState("");
  const [mode, setMode] = useState<Mode>("flight");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function go() {
    setErr("");
    setPlan(null);
    setBusy(true);
    try {
      setPlan(
        await api.plan({
          origin: from.trim(),
          destination: to.trim(),
          start,
          end,
          travelers: Math.min(9, Math.max(1, Number(people) || 1)),
          mode,
          budget: budget ? Number(budget) : undefined,
        }),
      );
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.pad} keyboardShouldPersistTaps="handled">
      <Text style={s.h1}>Plan my trip</Text>
      <Chips value={mode} onChange={setMode} options={[
        { value: "flight", label: "Fly" }, { value: "train", label: "Train" }, { value: "bus", label: "Bus" },
      ]} />
      <PlaceField label="From" value={from} onChange={setFrom} />
      <PlaceField label="To" value={to} onChange={setTo} />
      <DateField
        label="Start"
        value={start}
        onChange={(v) => {
          setStart(v);
          if (end <= v) setEnd(plusDaysFrom(v, 1));
        }}
      />
      <DateField label="End" value={end} onChange={setEnd} min={plusDaysFrom(start, 1)} />
      <Field label="Travellers" value={people} onChangeText={setPeople} keyboardType="number-pad" />
      <Field label="Total budget (optional)" value={budget} onChangeText={setBudget} keyboardType="number-pad" />
      <ErrorText msg={err} />
      <Button title="Build my plan" onPress={go} busy={busy} />
      {plan && (
        <View style={{ marginTop: 20 }}>
          <Text style={s.h1}>Total {money(plan.total, plan.currency)}</Text>
          <OfferCard o={plan.outbound} />
          <OfferCard o={plan.return} />
          <OfferCard o={plan.hotel} />
          <Card>
            {plan.itinerary.map((d) => (
              <Text key={d.day} style={{ marginVertical: 2 }}>Day {d.day}: {d.idea}</Text>
            ))}
          </Card>
          <Button
            title="Book everything"
            onPress={() => navigation.navigate("Book", { offerIds: [plan.outbound.offer_id, plan.return.offer_id, plan.hotel.offer_id] })}
          />
        </View>
      )}
    </ScrollView>
  );
}
