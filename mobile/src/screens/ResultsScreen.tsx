import React, { useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, Text, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api, clock, hm, money, Offer } from "../api";
import { Card, color, ErrorText, s } from "../ui";
import type { Stack } from "./SearchScreen";

export function OfferCard({ o }: { o: Offer }) {
  if (o.kind === "hotel") {
    return (
      <Card>
        <View style={s.row}>
          <Text style={{ fontWeight: "700", flex: 1 }}>{o.name}</Text>
          <Text style={{ color: color.ok, fontWeight: "700" }}>{o.rating} ★</Text>
        </View>
        <Text style={s.mute}>{"★".repeat(o.stars ?? 0)} · {o.amenities?.join(" · ")}</Text>
        <View style={[s.row, { marginTop: 8 }]}>
          <Text style={s.mute}>{money(o.price_per_night ?? 0, o.currency)}/night × {o.nights}</Text>
          <Text style={s.price}>{money(o.price, o.currency)}</Text>
        </View>
      </Card>
    );
  }
  return (
    <Card>
      <View style={s.row}>
        <Text style={{ fontWeight: "700" }}>{o.carrier} · {o.number}</Text>
        <Text style={s.price}>{money(o.price, o.currency)}</Text>
      </View>
      <View style={[s.row, { marginTop: 8 }]}>
        <Text style={{ fontSize: 18, fontWeight: "700" }}>{clock(o.departure!)}</Text>
        <Text style={s.mute}>{hm(o.duration_min ?? 0)}{o.stops ? ` · ${o.stops} stop` : " · direct"}</Text>
        <Text style={{ fontSize: 18, fontWeight: "700" }}>{clock(o.arrival!)}</Text>
      </View>
      <Text style={s.mute}>{o.origin} → {o.destination}{o.pax > 1 ? ` · ${o.pax} travellers` : ""}</Text>
    </Card>
  );
}

export default function ResultsScreen({ route, navigation }: NativeStackScreenProps<Stack, "Results">) {
  const [items, setItems] = useState<Offer[] | null>(null);
  const [err, setErr] = useState("");
  const p = route.params;

  useEffect(() => {
    const req =
      p.type === "hotel"
        ? api.hotels(p.city, p.checkIn, p.checkOut, p.guests)
        : api.transport(p.mode, p.origin, p.destination, p.date, p.adults);
    req.then(setItems).catch((e) => setErr(e.message));
  }, []);

  if (err) return <View style={s.pad}><ErrorText msg={err} /></View>;
  if (!items) return <ActivityIndicator style={{ marginTop: 40 }} color={color.brand} />;
  return (
    <FlatList
      style={s.screen}
      contentContainerStyle={s.pad}
      data={items}
      keyExtractor={(o) => o.offer_id}
      ListEmptyComponent={<Text style={s.mute}>No results. Try other dates.</Text>}
      renderItem={({ item }) => (
        <Pressable accessibilityRole="button" onPress={() => navigation.navigate("Book", { offerIds: [item.offer_id] })}>
          <OfferCard o={item} />
        </Pressable>
      )}
    />
  );
}
