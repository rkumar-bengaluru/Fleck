import { useEffect, useRef, useState } from "react";
import { api } from "../api";

/** Autosaves 1 s after the last keystroke, and again when the page is hidden or the editor unmounts with a change pending. */
export default function NotesEditor({ meetingId, initial }: { meetingId: string; initial: string }) {
  const [text, setText] = useState(initial);
  const [state, setState] = useState<"saved" | "saving" | "failed">("saved");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const pending = useRef<string | null>(null);
  const chain = useRef<Promise<unknown>>(Promise.resolve());  // one PUT at a time, in order
  const inflight = useRef(0);
  const touched = useRef(false);
  useEffect(() => { if (!touched.current) setText(initial); }, [initial]);  // a reconnect must not overwrite typing
  const save = () => {
    const t = pending.current;
    if (t === null) return;
    pending.current = null;
    inflight.current++;
    chain.current = chain.current.then(() => api(`/api/meetings/${meetingId}/notes`, { method: "PUT", body: { text: t } }))
      .then(() => { inflight.current--; setState(inflight.current || pending.current !== null ? "saving" : "saved"); })
      .catch(() => { inflight.current--; setState("failed"); });
  };
  useEffect(() => {
    window.addEventListener("pagehide", save);
    return () => { window.removeEventListener("pagehide", save); clearTimeout(timer.current); save(); };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section aria-labelledby="notes-h" className="live-card">
      <div className="live-card-head">
        <h2 id="notes-h">My notes</h2>
        <span className="live-hint" role="status">{state === "saved" ? "Saved" : state === "saving" ? "Saving…" : "Couldn't save. Still trying on your next edit."}</span>
      </div>
      <textarea aria-label="My notes" className="notes" value={text} onChange={e => {
        touched.current = true;
        setText(e.target.value);
        pending.current = e.target.value;
        setState("saving");
        clearTimeout(timer.current);
        timer.current = setTimeout(save, 1000);
      }} />
    </section>
  );
}
