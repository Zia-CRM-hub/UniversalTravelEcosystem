import React, { useRef, useState } from "react";
import { FlatList, KeyboardAvoidingView, Platform, Pressable, Text, TextInput, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { api, Card as ChatCard, money } from "../api";
import { Button, color, s } from "../ui";
import { OfferCard } from "./ResultsScreen";
import type { Stack } from "./SearchScreen";

interface Msg {
  id: number;
  from: "me" | "bot";
  text: string;
  cards?: ChatCard[];
}

export default function ChatScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<Stack>>();
  const nextId = useRef(1);
  const [msgs, setMsgs] = useState<Msg[]>([
    { id: 0, from: "bot", text: "Hi! Tell me where and when you want to travel, and I’ll find options." },
  ]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const list = useRef<FlatList<Msg>>(null);

  const push = (m: Omit<Msg, "id">) => setMsgs((prev) => [...prev, { ...m, id: nextId.current++ }]);

  async function send() {
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    push({ from: "me", text: t });
    setBusy(true);
    try {
      const r = await api.chat(t);
      push({ from: "bot", text: r.reply, cards: r.cards });
    } catch (e) {
      push({ from: "bot", text: (e as Error).message });
    } finally {
      setBusy(false);
      setTimeout(() => list.current?.scrollToEnd({ animated: true }), 50);
    }
  }

  return (
    <KeyboardAvoidingView style={s.screen} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={90}>
      <FlatList
        ref={list}
        data={msgs}
        keyExtractor={(m) => String(m.id)}
        contentContainerStyle={s.pad}
        ListHeaderComponent={<Text style={s.h1}>Assistant</Text>}
        renderItem={({ item: m }) => (
          <View style={{ alignSelf: m.from === "me" ? "flex-end" : "flex-start", maxWidth: "92%", marginBottom: 10 }}>
            <View style={{ backgroundColor: m.from === "me" ? color.brand : "#fff", borderRadius: 16, padding: 12, borderWidth: m.from === "me" ? 0 : 1, borderColor: color.line }}>
              <Text style={{ color: m.from === "me" ? "#fff" : color.ink }}>{m.text}</Text>
            </View>
            {m.cards?.map((c, i) =>
              c.type === "offer" ? (
                <Pressable key={i} accessibilityRole="button" onPress={() => navigation.navigate("Book", { offerIds: [c.offer.offer_id] })}>
                  <OfferCard o={c.offer} />
                  <Text style={{ color: color.brand, fontWeight: "700", marginTop: -6, marginBottom: 6 }}>Tap to book option {c.n}</Text>
                </Pressable>
              ) : (
                <View key={i}>
                  <Text style={{ fontWeight: "700", marginTop: 8 }}>Full trip · {money(c.total, c.currency)}</Text>
                  {c.offers.map((o) => <OfferCard key={o.offer_id} o={o} />)}
                  <Button title="Book everything" onPress={() => navigation.navigate("Book", { offerIds: c.offers.map((o) => o.offer_id) })} />
                </View>
              ),
            )}
          </View>
        )}
        ListFooterComponent={busy ? <Text style={s.mute}>Thinking…</Text> : null}
      />
      <View style={{ flexDirection: "row", gap: 8, padding: 12, borderTopWidth: 1, borderColor: color.line, backgroundColor: "#fff" }}>
        <TextInput
          style={[s.input, { flex: 1 }]}
          value={text}
          onChangeText={setText}
          placeholder="Cheapest flight Delhi to Goa Friday"
          placeholderTextColor="#9CA3AF"
          maxLength={2000}
          onSubmitEditing={send}
          returnKeyType="send"
        />
        <Button title="Send" onPress={send} busy={busy} />
      </View>
    </KeyboardAvoidingView>
  );
}
