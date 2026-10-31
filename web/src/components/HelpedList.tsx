import { mmss, type Marker } from "./RecordingTimeline";
import SourceChip from "./SourceChip";
import type { Citation } from "../events";
import type { Kind } from "./SourceChip";

export interface Helped { assist_id: string; question: string; answer: string; kind: Kind; citations: Citation[]; used: boolean | null; used_by: string | null; at_ms: number; late?: boolean }

const label = (a: Helped) => `${a.used ? "Used" : "Not used"}, ${a.used_by === "manual" ? "by hand" : "auto"}`;

export default function HelpedList({ items, canSeek, onSeek, onToggle }: { items: Helped[]; canSeek: boolean; onSeek: (ms: number) => void; onToggle: (a: Helped) => void }) {
  const used = items.filter(a => a.used).length;
  return (
    <section className="after-card" aria-labelledby="helped">
      <div className="after-card-head"><h2 id="helped">Where Fleck helped</h2><span className="muted">{items.length} answers, {used} used</span></div>
      {items.length === 0 && <p className="muted">Fleck gave no answers in this meeting.</p>}
      {items.map(a => (
        <article key={a.assist_id} className="helped" aria-label={a.question}>
          {canSeek
            ? <button type="button" className="link" aria-label={`Play from ${mmss(a.at_ms)}`} onClick={() => onSeek(a.at_ms)}>{mmss(a.at_ms)}</button>
            : <span className="muted">{mmss(a.at_ms)}</span>}
          <div className="helped-body">
            <div className="helped-q">{a.question}</div>
            {a.answer && <div className="helped-a">{a.answer}</div>}
            <SourceChip kind={a.kind} citations={a.citations} late={a.late} />
          </div>
          <button type="button" className={`used ${a.used ? "on" : ""}`} onClick={() => onToggle(a)}>{label(a)}</button>
        </article>
      ))}
    </section>
  );
}
