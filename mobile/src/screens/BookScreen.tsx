import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, Text, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api, ApiError, Checkout, Contact, money, Offer, Passenger, PaymentResult } from "../api";
import { DateField } from "../inputs";
import { payWithRazorpay } from "../razorpay";
import { Button, Card, Chips, color, ErrorText, Field, s } from "../ui";
import { OfferCard } from "./ResultsScreen";
import type { Stack } from "./SearchScreen";

const blank = (): Passenger => ({ title: "mr", gender: "m", given_name: "", family_name: "", born_on: "1990-01-01" });
const today = () => new Date().toISOString().slice(0, 10);

/** Books one offer, or a whole planned trip, for the same travellers with ONE payment. */
export default function BookScreen({ route, navigation }: NativeStackScreenProps<Stack, "Book">) {
  const { offerIds } = route.params;
  const [offers, setOffers] = useState<Offer[] | null>(null);
  const [pax, setPax] = useState<Passenger[]>([]);
  const [contact, setContact] = useState<Contact>({ email: "", phone: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // After a network failure retry the SAME checkout and reuse the payment already made (never charge twice).
  const checkout = useRef<Checkout | null>(null);
  const paid = useRef<PaymentResult | null>(null);

  useEffect(() => {
    Promise.all(offerIds.map(api.offer))
      .then((o) => {
        setOffers(o);
        setPax(Array.from({ length: o[0]?.pax ?? 1 }, blank));
      })
      .catch((e) => setErr(e.message));
  }, []);

  const set = (i: number, k: keyof Passenger, v: string) => setPax(pax.map((p, j) => (j === i ? { ...p, [k]: v } : p)));

  async function confirm() {
    if (!offers) return;
    if (pax.some((p) => !p.given_name.trim() || !p.family_name.trim()))
      return setErr("Enter the given and family name of every traveller");
    if (!/^\+[1-9]\d{6,14}$/.test(contact.phone) || !contact.email.includes("@"))
      return setErr("Enter a valid email and a mobile number with country code, e.g. +919876543210");
    setErr("");
    setBusy(true);
    try {
      const co = (checkout.current ??= await api.checkout(offerIds));
      if (co.gateway === "razorpay" && !paid.current) {
        paid.current = await payWithRazorpay(co, { email: contact.email, contact: contact.phone });
        if (!paid.current) return; // customer closed the payment sheet
      }
      const res = await api.confirm(co.id, paid.current, pax, contact);
      Alert.alert("Booked!", res.bookings.map((b) => b.reference).join("\n"));
      navigation.popToTop();
    } catch (e) {
      if (e instanceof ApiError) {
        checkout.current = null;
        paid.current = null;
        setErr(e.message);
      } else {
        setErr((e as Error).message || "Network problem. Tap again to retry safely.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (!offers) return err ? <View style={s.pad}><ErrorText msg={err} /></View> : <ActivityIndicator style={{ marginTop: 40 }} color={color.brand} />;
  const total = offers.reduce((sum, o) => sum + o.price, 0);

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.pad} keyboardShouldPersistTaps="handled">
      {offers.map((o) => <OfferCard key={o.offer_id} o={o} />)}
      <Text style={s.h1}>{pax.length > 1 ? "Travellers" : "Traveller"}</Text>
      <Text style={[s.mute, { marginBottom: 8 }]}>Names exactly as on the travel document.</Text>
      {pax.map((p, i) => (
        <Card key={i}>
          <Text style={{ fontWeight: "700", marginBottom: 8 }}>Traveller {i + 1}</Text>
          <Chips value={p.title} onChange={(v) => set(i, "title", v)} options={["mr", "ms", "mrs", "miss"].map((t) => ({ value: t, label: t }))} />
          <Chips
            value={p.gender}
            onChange={(v) => set(i, "gender", v)}
            options={[{ value: "m", label: "Male" }, { value: "f", label: "Female" }]}
          />
          <Field label="Given name" value={p.given_name} onChangeText={(v) => set(i, "given_name", v)} autoCapitalize="words" />
          <Field label="Family name" value={p.family_name} onChangeText={(v) => set(i, "family_name", v)} autoCapitalize="words" />
          <DateField label="Date of birth" past max={today()} value={p.born_on} onChange={(v) => set(i, "born_on", v)} />
        </Card>
      ))}
      <Text style={s.h1}>Contact</Text>
      <Field label="Email" value={contact.email} onChangeText={(v) => setContact({ ...contact, email: v })} keyboardType="email-address" />
      <Field label="Mobile (+country code)" value={contact.phone} onChangeText={(v) => setContact({ ...contact, phone: v })} keyboardType="phone-pad" placeholder="+919876543210" />
      <View style={{ height: 8 }} />
      <ErrorText msg={err} />
      <Button title={`Pay ${money(total, offers[0].currency)}`} onPress={confirm} busy={busy} />
      <Text style={[s.mute, { marginTop: 8, textAlign: "center" }]}>You are charged only after every booking is confirmed.</Text>
    </ScrollView>
  );
}
