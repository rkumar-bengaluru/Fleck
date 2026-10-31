import { useCallback, useEffect, useRef, useState } from "react";
import { api, setTitle } from "../api";
import HelpedList, { type Helped } from "../components/HelpedList";
import RecordingTimeline from "../components/RecordingTimeline";
import SummaryNotes, { type Section } from "../components/SummaryNotes";
import TranscriptView, { type Line } from "../components/TranscriptView";
import type { Citation } from "../events";
import type { Kind } from "../components/SourceChip";

interface Detail {
  meeting: { id: number; title: string; status: string; failure_reason: string | null; started_at: string; ended_at: string | null; recorded_by_name: string };
  segments: (Line & { end_ms: number })[];
  assists: { assist_id: string; segment_id: string; question: string; answer: string; kind: Kind; citations: Citation[]; used: boolean | null; used_by: string | null }[];
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
  const audio = useRef<HTMLAudioElement>(null);
  const load = useCallback(() => api<Detail>(`/api/meetings/${id}`).then(x => { setD(x); setError(false); }).catch(() => setError(true)), [id]);
  useEffect(() => { void load(); }, [load]);
  // the worker cannot push to browsers (decision 120d): poll until it is done
  const status = d?.meeting.status;
  useEffect(() => { if (status !== "processing") return; const t = setInterval(() => void load(), 3000); return () => clearInterval(t); }, [status, load]);
  useEffect(() => { if (d) setTitle(d.meeting.title); }, [d?.meeting.title]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!d) return error ? <p className="error" role="alert">Couldn't load this meeting.</p> : null;

  const { meeting: m } = d;
  const at = new Map(d.segments.map(s => [s.segment_id, s.start_ms]));
  const items: Helped[] = d.assists.map(a => ({ ...a, at_ms: at.get(a.segment_id) ?? 0 }));
  const span = m.ended_at ? new Date(m.ended_at).getTime() - new Date(m.started_at).getTime() : 0;
  const seek = (ms: number) => { const a = audio.current; if (a) { a.currentTime = ms / 1000; void a.play(); } };
  const toggle = (a: Helped) => {
    const used = !a.used;
    const set = (u: boolean | null, by: string | null) => setD(x => x && { ...x, assists: x.assists.map(y => (y.assist_id === a.assist_id ? { ...y, used: u, used_by: by } : y)) });
    set(used, "manual");
    api(`/api/assists/${a.assist_id}`, { method: "PATCH", body: { used } }).catch(() => set(a.used, a.used_by));
  };
  const tabs: Tab[] = ["Notes", "Transcript", "Chat"];
  return (
    <>
      <header className="after-head">
        <div className="after-title">
          <h1>{m.title}</h1>
          <div className="subtitle">{when(m.started_at)}. {Math.round(span / 60_000)} min. Recorded by {m.recorded_by_name || "someone"}.</div>
        </div>
        <span className="muted">Claude cost ${d.cost_usd.toFixed(2)}</span>
      </header>
      {m.status === "ready" && <RecordingTimeline src={`/api/meetings/${id}/recording`} audio={audio} totalMs={span || Math.max(0, ...d.segments.map(s => s.end_ms))}
        markers={items.map(a => ({ id: a.assist_id, at_ms: a.at_ms, kind: a.kind }))} onSeek={seek} />}
      <nav aria-label="Meeting views" className="tabs">
        {tabs.map(t => <button key={t} type="button" aria-current={tab === t ? "page" : undefined} onClick={() => setTab(t)}>{t}</button>)}
      </nav>
      {m.status === "processing" && <p role="status" className="after-card">Processing the recording…</p>}
      {m.status === "failed" && (
        <div role="alert" className="after-card error">
          Processing failed: {m.failure_reason || "unknown reason"}{" "}
          <button type="button" className="btn" onClick={() => api(`/api/meetings/${id}/retry`, { method: "POST" }).then(load).catch(() => setError(true))}>Try again</button>
        </div>
      )}
      {tab === "Notes" && m.status !== "processing" && <><HelpedList items={items} onSeek={seek} onToggle={toggle} /><SummaryNotes sections={d.summary} /></>}
      {tab === "Transcript" && <TranscriptView segments={d.segments} onSeek={seek} />}
      {tab === "Chat" && <p className="after-card muted">Chat arrives in a later build</p>}
    </>
  );
}
