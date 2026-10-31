import { useEffect, useRef } from "react";
import type { SegmentEvent } from "../events";
import AssistCard, { type AssistView } from "./AssistCard";

export type Seg = Pick<SegmentEvent, "segment_id" | "stream" | "start_ms" | "text" | "final">;

const stamp = (ms: number) => `${Math.floor(ms / 60_000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

export default function Transcript({ segments, assists }: { segments: Seg[]; assists: AssistView[] }) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);  // follows the newest line until the user scrolls up
  const onScroll = () => { const b = box.current!; stick.current = b.scrollHeight - b.scrollTop - b.clientHeight < 40; };
  useEffect(() => { if (stick.current) box.current?.scrollTo?.({ top: box.current.scrollHeight }); }, [segments, assists]);
  const known = new Set(segments.map(s => s.segment_id));
  const orphans = assists.filter(a => !known.has(a.segment_id));
  return (
    <div className="live-scroll" ref={box} onScroll={onScroll}>
      <ol className="transcript" aria-label="Live transcript">
        {segments.map(s => {
          const mine = assists.filter(a => a.segment_id === s.segment_id);
          return (
            <li key={s.segment_id} className="line">
              <span className="line-who">{stamp(s.start_ms)}<br /><b className={s.stream === "you" ? "you" : "client"}>{s.stream === "you" ? "You" : "Client"}</b></span>
              <span className="line-body">
                <span className={s.final ? (mine.length ? "question" : "") : "partial"}>{s.text}</span>
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
