import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

export interface ChatCitation { type: "meeting" | "document"; n: number; label: string; meeting_id?: number; start_ms?: number; doc_id?: string }
interface Message { role: "user" | "assistant"; text: string; citations: ChatCitation[] }

/** Where a citation opens: the meeting's page at that time (it seeks the audio), or the document's row in the Library. */
export const citationHref = (c: ChatCitation) => c.type === "meeting"
  ? `/meetings/${c.meeting_id}${c.start_ms != null ? `?t=${c.start_ms}` : ""}` : `/library?doc=${c.doc_id}`;

/** Chat about one meeting (`meetingId`) or across all of them (null). One saved thread each. */
export default function ChatPanel({ meetingId }: { meetingId: number | null }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const label = meetingId === null ? "Ask across meetings" : "Ask about this meeting";
  useEffect(() => { api<Message[]>(`/api/chat${meetingId === null ? "" : `?meeting_id=${meetingId}`}`).then(m => setMessages(m ?? [])).catch(() => setFailed(true)); }, [meetingId]);
  useEffect(() => { end.current?.scrollIntoView?.({ block: "nearest" }); }, [messages, busy]);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    const question = text.trim();
    if (!question || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const r = await api<{ text: string; citations: ChatCitation[] }>("/api/chat", { method: "POST", body: { meeting_id: meetingId, text: question } });
      setMessages(m => [...m, { role: "user", text: question, citations: [] }, { role: "assistant", ...r }]);
      setText("");
    } catch {
      setFailed(true);  // the question stays in the box
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="after-card chat" aria-labelledby="chat-h">
      <h2 id="chat-h">{label}</h2>
      <div className="chat-log" role="log" aria-live="polite">
        {messages.length === 0 && !busy && <p className="muted">{meetingId === null ? "Ask what was said in any recorded meeting, or what the library says." : "Ask what was said in this meeting."}</p>}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role}`}>
            <p>{m.text}</p>
            {m.citations.length > 0 && <ul className="chat-cites" aria-label="Sources">
              {m.citations.map(c => <li key={c.n}>[{c.n}] <Link to={citationHref(c)}>{c.label}</Link></li>)}
            </ul>}
          </div>
        ))}
        {busy && <p className="muted" role="status">Thinking…</p>}
        <div ref={end} />
      </div>
      {failed && <p className="error" role="alert">Couldn't get an answer. Try again.</p>}
      <form className="chat-form" onSubmit={ask}>
        <input aria-label={label} placeholder={label} value={text} onChange={e => setText(e.target.value)} />
        <button type="submit" className="btn-primary" disabled={busy}>Ask</button>
      </form>
    </section>
  );
}
