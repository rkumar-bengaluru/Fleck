import { useEffect } from "react";
import { mmss } from "./RecordingTimeline";

export interface Line { segment_id: string; stream: string; start_ms: number; text: string }

export default function TranscriptView({ segments, canSeek, onSeek, focusMs }: { segments: Line[]; canSeek: boolean; onSeek: (ms: number) => void; focusMs?: number }) {
  // the line a chat citation points at: the last one starting at or before that time
  const focus = focusMs === undefined ? undefined : segments.filter(s => s.start_ms <= focusMs).at(-1)?.segment_id;
  useEffect(() => { if (focus) document.getElementById(`line-${focus}`)?.scrollIntoView?.({ block: "center" }); }, [focus]);
  return (
    <section className="after-card" aria-labelledby="tx">
      <div className="after-card-head"><h2 id="tx">Transcript</h2></div>
      {segments.length === 0 && <p className="muted">No transcript was kept.</p>}
      <ol className="transcript">
        {segments.map(s => (
          <li key={s.segment_id} id={`line-${s.segment_id}`} className="line" aria-current={s.segment_id === focus ? "true" : undefined}>
            <span className="line-who">{canSeek ? <button type="button" className="link" aria-label={`Play from ${mmss(s.start_ms)}`} onClick={() => onSeek(s.start_ms)}>{mmss(s.start_ms)}</button> : mmss(s.start_ms)}<br />
              <b className={s.stream === "you" ? "you" : "client"}>{s.stream === "you" ? "You" : "Client"}</b></span>
            <span className="line-body">{s.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
