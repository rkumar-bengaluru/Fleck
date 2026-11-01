import { useEffect, useRef, useState } from "react";
import type { SegmentEvent } from "../events";
import AssistCard, { type AssistView } from "./AssistCard";

export type Seg = Pick<SegmentEvent, "segment_id" | "stream" | "start_ms" | "text" | "final">;

// spec 7.7: the helper sends a capture outage as a segment with a `gap-` id; it is a marker, not speech
export const isGap = (s: { segment_id: string }) => s.segment_id.startsWith("gap-");
export const Who = ({ s }: { s: { segment_id: string; stream: string } }) =>
  isGap(s) ? <b className="gap">Fleck</b> : <b className={s.stream === "you" ? "you" : "client"}>{s.stream === "you" ? "You" : "Client"}</b>;

const stamp = (ms: number) => `${Math.floor(ms / 60_000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

export default function Transcript({ segments, assists }: { segments: Seg[]; assists: AssistView[] }) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);  // follows the newest line until the user scrolls up
  const onScroll = () => { const b = box.current!; stick.current = b.scrollHeight - b.scrollTop - b.clientHeight < 40; };
  useEffect(() => { if (stick.current) box.current?.scrollTo?.({ top: box.current.scrollHeight }); }, [segments, assists]);
  const seen = useRef<Set<string> | null>(null);
  const [said, setSaid] = useState("");  // the polite live region announces each answer once, when it finishes, never the transcript lines
  useEffect(() => {
    const done = assists.filter(a => a.kind !== null);
    if (seen.current === null) { seen.current = new Set(done.map(a => a.assist_id)); return; }  // what was there on load is not news
    const fresh = done.filter(a => !seen.current!.has(a.assist_id));
    fresh.forEach(a => seen.current!.add(a.assist_id));
    if (fresh.length) setSaid(fresh.map(a => a.text || "Not in our material").join(" "));
  }, [assists]);
  const known = new Set(segments.map(s => s.segment_id));
  const orphans = assists.filter(a => !known.has(a.segment_id));
  return (
    <div className="live-scroll" ref={box} onScroll={onScroll} tabIndex={0} role="region" aria-label="Live transcript">
      <div className="sr-only" aria-live="polite" role="status">{said}</div>
      <ol className="transcript">
        {segments.map(s => {
          const mine = assists.filter(a => a.segment_id === s.segment_id);
          return (
            <li key={s.segment_id} className="line">
              <span className="line-who">{stamp(s.start_ms)}<br /><Who s={s} /></span>
              <span className="line-body">
                <span className={isGap(s) ? "gap" : s.final ? (mine.length ? "question" : "") : "partial"}>{s.text}</span>
                {mine.map(a => <AssistCard key={a.assist_id} a={a} />)}
              </span>
            </li>
          );
        })}
        {orphans.map(a => <li key={a.assist_id} className="line"><span className="line-who" /><span className="line-body"><AssistCard a={a} /></span></li>)}
      </ol>
    </div>
  );
}
