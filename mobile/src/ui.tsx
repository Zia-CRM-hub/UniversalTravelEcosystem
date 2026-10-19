import React from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
} from "react-native";

export const color = { brand: "#0B6EFD", ink: "#111827", mute: "#6B7280", bg: "#F5F7FB", line: "#E5E7EB", bad: "#DC2626", ok: "#16A34A" };

export function Button({ title, onPress, busy, kind = "primary" }: {
  title: string;
  onPress: () => void;
  busy?: boolean;
  kind?: "primary" | "ghost";
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={busy}
      onPress={onPress}
      style={[s.btn, kind === "ghost" && s.ghost, busy && { opacity: 0.6 }]}
    >
      {busy ? (
        <ActivityIndicator color={kind === "ghost" ? color.brand : "#fff"} />
      ) : (
        <Text style={[s.btnText, kind === "ghost" && { color: color.brand }]}>{title}</Text>
      )}
    </Pressable>
  );
}

export function Field({ label, ...p }: { label: string } & TextInputProps) {
  return (
    <View style={{ marginBottom: 12 }}>
      <Text style={s.label}>{label}</Text>
      <TextInput style={s.input} placeholderTextColor="#9CA3AF" autoCapitalize="none" {...p} />
    </View>
  );
}

export const Card = ({ children }: { children: React.ReactNode }) => <View style={s.card}>{children}</View>;

export const ErrorText = ({ msg }: { msg: string }) =>
  msg ? <Text style={{ color: color.bad, marginBottom: 8 }}>{msg}</Text> : null;

export function Chips<T extends string>({ options, value, onChange }: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <View style={{ flexDirection: "row", gap: 8, marginBottom: 14 }}>
      {options.map((o) => (
        <Pressable key={o.value} onPress={() => onChange(o.value)} style={[s.chip, value === o.value && s.chipOn]}>
          <Text style={{ color: value === o.value ? "#fff" : color.ink, fontWeight: "600" }}>{o.label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

export const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: color.bg },
  pad: { padding: 16 },
  h1: { fontSize: 24, fontWeight: "800", color: color.ink, marginBottom: 12 },
  label: { fontSize: 12, color: color.mute, marginBottom: 4, fontWeight: "600" },
  input: { backgroundColor: "#fff", borderWidth: 1, borderColor: color.line, borderRadius: 10, padding: 12, fontSize: 16, color: color.ink },
  btn: { backgroundColor: color.brand, padding: 14, borderRadius: 12, alignItems: "center", marginTop: 6 },
  ghost: { backgroundColor: "transparent", borderWidth: 1, borderColor: color.brand },
  btnText: { color: "#fff", fontWeight: "700", fontSize: 16 },
  card: { backgroundColor: "#fff", borderRadius: 14, padding: 14, marginBottom: 12, borderWidth: 1, borderColor: color.line },
  chip: { paddingVertical: 8, paddingHorizontal: 16, borderRadius: 20, backgroundColor: "#fff", borderWidth: 1, borderColor: color.line },
  chipOn: { backgroundColor: color.brand, borderColor: color.brand },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  price: { fontSize: 18, fontWeight: "800", color: color.ink },
  mute: { color: color.mute },
});
