import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";

export default function LoginPage({ onDone }: { onDone: () => void }) {
  const nav = useNavigate();
  const next = (useLocation().state as { next?: string } | null)?.next ?? "/";
  const [register, setRegister] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      if (register) await api.register(email.trim(), name.trim(), password);
      else await api.login(email.trim(), password);
      onDone();
      nav(next, { replace: true });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel narrow" onSubmit={submit}>
      <h1>{register ? "Create your account" : "Welcome back"}</h1>
      {register && (
        <label className="field"><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} required /></label>
      )}
      <label className="field"><span>Email</span><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label className="field">
        <span>Password (8+ characters)</span>
        <input type="password" minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} required />
      </label>
      {err && <p className="err" role="alert">{err}</p>}
      <button className="btn" disabled={busy}>{busy ? "Please wait…" : register ? "Sign up" : "Log in"}</button>
      <button type="button" className="btn ghost" onClick={() => setRegister(!register)}>
        {register ? "I already have an account" : "New here? Sign up"}
      </button>
    </form>
  );
}
