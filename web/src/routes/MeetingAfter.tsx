import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, setTitle } from "../api";
import ChatPanel from "../components/ChatPanel";
import HelpedList, { type Helped } from "../components/HelpedList";
import RecordingTimeline from "../components/RecordingTimeline";
import SummaryNotes, { type Section } from "../components/SummaryNotes";
import TranscriptView, { type Line } from "../components/TranscriptView";
import type { Citation } from "../events";
import type { Kind } from "../components/SourceChip";

interface Detail {
  meeting: { id: number; title: string; status: string; failure_reason: string | null; started_at: string; ended_at: string | null; recorded_by_name: string };
  segments: (Line & { end_ms: number })[];
  assists: { assist_id: string; segment_id: string; question: string; answer: string; kind: Kind; citations: Citation[]; used: boolean | null; used_by: string | null; late?: boolean }[];
  summary: Section[] | null;
  cost_usd: number;
}
type Tab = "Notes" | "Transcript" | "Chat";

const when = (iso: string) => { const d = new Date(iso);
  return `${d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}, ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`; };

/** The page for a meeting that has ended: processing, ready or failed (Task 19). */
export default function MeetingAfter({ id }: { id: string }) {
  const [d, setD] = useState<Detail | null>(null);
  const [tab, setTab] = useState<Tab>("Notes");
  const [error, setError] = useState(false);
  const [broken, setBroken] = useState(false);  // the audio element failed
  const [note, setNote] = useState("");  // an inline message after a failed save or retry
  const audio = useRef<HTMLAudioElement>(null);
  const t = Number(useSearchParams()[0].get("t") ?? NaN);  // a chat citation opens the transcript at this time
  const load = useCallback(() => api<Detail>(`/api/meetings/${id}`).then(x => { setD(x); setError(false); }).catch(() => setError(true)), [id]);
  useEffect(() => { void load(); }, [load]);
  // the worker cannot push to browsers (decision 120d): poll until it is done
  const status = d?.meeting.status;
  useEffect(() => { if (status !== "processing") return; const t = setInterval(() => void load(), 3000); return () => clearInterval(t); }, [status, load]);
  const ready = d?.meeting.status === "ready";
  useEffect(() => {
    if (Number.isNaN(t) || !d) return;
    setTab("Transcript");
    const a = audio.current;
    if (a && ready) { a.currentTime = t / 1000; a.play().catch(() => {}); }
  }, [t, d !== null]);  // eslint-disable-line react-hooks/exhaustive-deps  (once per t: a later switch to ready must not force the tab)  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (d) setTitle(d.meeting.title); }, [d?.meeting.title]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!d) return error ? <p className="error" role="alert">Couldn't load this meeting.</p> : null;

  const { meeting: m } = d;
  const at = new Map(d.segments.map(s => [s.segment_id, s.start_ms]));
  const items: Helped[] = d.assists.map(a => ({ ...a, at_ms: at.get(a.segment_id) ?? 0 }));
  const span = m.ended_at ? new Date(m.ended_at).getTime() - new Date(m.started_at).getTime() : 0;
  const canSeek = m.status === "ready" && !broken;
  const seek = (ms: number) => { const a = audio.current; if (a && canSeek) { a.currentTime = ms / 1000; a.play().catch(() => {}); } };
  const toggle = (a: Helped) => {
    const used = !a.used;
    setNote("");
    setD(x => x && { ...x, assists: x.assists.map(y => (y.assist_id === a.assist_id ? { ...y, used, used_by: "manual" } : y)) });
    // on failure the stored state is the truth, whatever other clicks came in meanwhile
    api(`/api/assists/${a.assist_id}`, { method: "PATCH", body: { used } }).catch(() => { setNote("Couldn't save that change. Try again."); void load(); });
  };
  const tabs: Tab[] = ["Notes", "Transcript", "Chat"];
  return (
    <>
      <header className="after-head">
        <div className="after-title">
          <h1>{m.title}</h1>
          <div className="subtitle">{when(m.started_at)}. {Math.round(span / 60_000)} min. Recorded by {m.recorded_by_name}.</div>
        </div>
        <span className="muted">Claude cost ${d.cost_usd.toFixed(2)}</span>
      </header>
      {m.status === "ready" && <RecordingTimeline src={`/api/meetings/${id}/recording`} audio={audio} totalMs={span || Math.max(0, ...d.segments.map(s => s.end_ms))}
        markers={items.map(a => ({ id: a.assist_id, at_ms: a.at_ms, kind: a.kind }))} canSeek={canSeek} onSeek={seek} onBroken={() => setBroken(true)} />}
      <div role="tablist" aria-label="Meeting views" className="tabs">
        {tabs.map(t => <button key={t} type="button" role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls="tabpanel" tabIndex={tab === t ? 0 : -1}
          onClick={() => setTab(t)}>{t}</button>)}
      </div>
      {note && <p className="error" role="alert">{note}</p>}
      {m.status === "processing" && <p role="status" className="after-card">Processing the recording…</p>}
      {m.status === "failed" && (
        <div role="alert" className="after-card error">
          Processing failed: {m.failure_reason || "unknown reason"}{" "}
          <button type="button" className="btn" onClick={() => { setNote(""); api(`/api/meetings/${id}/retry`, { method: "POST" }).then(load).catch(() => setNote("Couldn't start processing again. Try again.")); }}>Try again</button>
        </div>
      )}
      <div role="tabpanel" id="tabpanel" aria-labelledby={`tab-${tab}`} className="tabpanel">
        {tab === "Notes" && m.status !== "processing" && <><HelpedList items={items} canSeek={canSeek} onSeek={seek} onToggle={toggle} /><SummaryNotes sections={d.summary} /></>}
        {tab === "Transcript" && <TranscriptView segments={d.segments} canSeek={canSeek} onSeek={seek} focusMs={Number.isNaN(t) ? undefined : t} />}
        {tab === "Chat" && <ChatPanel meetingId={m.id} />}
      </div>
    </>
  );
}
