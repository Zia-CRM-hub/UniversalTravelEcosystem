import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, Text, View } from "react-native";
import { api } from "../api";
import { Button, ErrorText, Field, s } from "../ui";

export default function AuthScreen({ onDone }: { onDone: () => void }) {
  const [register, setRegister] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setErr("");
    setBusy(true);
    try {
      if (register) await api.register(email.trim(), name.trim(), password);
      else await api.login(email.trim(), password);
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView style={s.screen} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView contentContainerStyle={[s.pad, { paddingTop: 80 }]} keyboardShouldPersistTaps="handled">
        <Text style={[s.h1, { fontSize: 32 }]}>Wanderly</Text>
        <Text style={[s.mute, { marginBottom: 24 }]}>Flights, trains, buses and stays. One simple trip.</Text>
        {register && <Field label="Name" value={name} onChangeText={setName} autoCapitalize="words" />}
        <Field label="Email" value={email} onChangeText={setEmail} keyboardType="email-address" />
        <Field label="Password (8+ characters)" value={password} onChangeText={setPassword} secureTextEntry />
        <ErrorText msg={err} />
        <Button title={register ? "Create account" : "Log in"} onPress={submit} busy={busy} />
        <View style={{ height: 8 }} />
        <Button
          kind="ghost"
          title={register ? "I already have an account" : "New here? Sign up"}
          onPress={() => setRegister(!register)}
        />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
