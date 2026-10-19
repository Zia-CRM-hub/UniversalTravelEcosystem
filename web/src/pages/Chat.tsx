import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, Card, money } from "../api";
import OfferCard from "../components/OfferCard";

interface Msg {
  from: "me" | "bot";
  text: string;
  cards?: Card[];
}

/** Hand-off links are absolute (so they work from WhatsApp); inside the app we only need the path. */
const toPath = (link: string) => {
  const u = new URL(link, window.location.origin);
  return u.pathname + u.search;
};

export default function ChatPage() {
  const [msgs, setMsgs] = useState<Msg[]>([{ from: "bot", text: "Hi! Tell me where and when you want to travel, and I’ll find options." }]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);

  async function send(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    setMsgs((m) => [...m, { from: "me", text: t }]);
    setBusy(true);
    try {
      const r = await api.chat(t);
      setMsgs((m) => [...m, { from: "bot", text: r.reply, cards: r.cards }]);
    } catch (e) {
      setMsgs((m) => [...m, { from: "bot", text: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel chat">
      <div className="row">
        <h1>Travel assistant</h1>
        <button className="btn small ghost" onClick={() => api.resetChat().then(() => setMsgs(msgs.slice(0, 1)))}>New chat</button>
      </div>
      <div className="log" aria-live="polite">
        {msgs.map((m, i) => (
          <div key={i} className={`bubble ${m.from}`}>
            <p>{m.text}</p>
            {m.cards?.map((c, j) =>
              c.type === "offer" ? (
                <OfferCard key={j} o={c.offer} action={<Link className="btn small" to={toPath(c.link)}>Book option {c.n}</Link>} />
              ) : (
                <div className="card" key={j}>
                  <strong>Full trip · {money(c.total, c.currency)}</strong>
                  {c.offers.map((o) => <OfferCard key={o.offer_id} o={o} />)}
                  <Link className="btn small" to={toPath(c.link)}>Book everything</Link>
                </div>
              ),
            )}
          </div>
        ))}
        {busy && <div className="bubble bot mute">Thinking…</div>}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={send}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Cheapest flight Delhi to Goa next Friday" maxLength={2000} />
        <button className="btn" disabled={busy}>Send</button>
      </form>
    </section>
  );
}
