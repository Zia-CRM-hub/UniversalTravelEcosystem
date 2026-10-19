import React, { useCallback, useState } from "react";
import { Alert, FlatList, Text, View } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { api, Booking, money } from "../api";
import { Button, Card, color, ErrorText, s } from "../ui";

export default function TripsScreen({ onLogout }: { onLogout: () => void }) {
  const [items, setItems] = useState<Booking[]>([]);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    api.bookings().then(setItems).catch((e) => setErr(e.message));
  }, []);
  useFocusEffect(load);

  function cancel(b: Booking) {
    Alert.alert("Cancel booking?", b.reference, [
      { text: "Keep" },
      {
        text: "Cancel booking",
        style: "destructive",
        onPress: () => api.cancel(b.id).then(load).catch((e) => setErr(e.message)),
      },
    ]);
  }

  return (
    <FlatList
      style={s.screen}
      contentContainerStyle={s.pad}
      data={items}
      keyExtractor={(b) => b.id}
      ListHeaderComponent={<><Text style={s.h1}>My trips</Text><ErrorText msg={err} /></>}
      ListEmptyComponent={<Text style={s.mute}>No bookings yet. Search to get started.</Text>}
      ListFooterComponent={<Button kind="ghost" title="Log out" onPress={onLogout} />}
      renderItem={({ item: b }) => (
        <Card>
          <View style={s.row}>
            <Text style={{ fontWeight: "700" }}>
              {b.offer.kind === "hotel" ? b.offer.name : `${b.offer.origin} → ${b.offer.destination}`}
            </Text>
            <Text style={{ color: b.status === "confirmed" ? color.ok : color.bad, fontWeight: "700" }}>
              {b.status}
            </Text>
          </View>
          <Text style={s.mute}>{b.reference} · {b.kind}</Text>
          <Text style={s.price}>{money(b.total_price, b.currency)}</Text>
          {b.status === "confirmed" && <Button kind="ghost" title="Cancel" onPress={() => cancel(b)} />}
        </Card>
      )}
    />
  );
}
