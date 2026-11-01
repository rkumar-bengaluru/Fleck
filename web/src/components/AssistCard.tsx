import type { Citation } from "../events";
import SourceChip, { type Kind } from "./SourceChip";

export interface AssistView {
  assist_id: string; segment_id: string; question: string; text: string; kind: Kind; citations: Citation[]; first_words_ms: number | null; late?: boolean;
}

export default function AssistCard({ a }: { a: AssistView }) {
  return (
    <span className="assist" role="group" aria-label={`Answer to: ${a.question}`}>
      {a.text && <span className="assist-text">{a.text}</span>}
      <span className="assist-meta">
        <SourceChip kind={a.kind} citations={a.citations} late={a.late} />
        {a.first_words_ms !== null && <span className="chip-note">First words {(a.first_words_ms / 1000).toFixed(1)} s after the question</span>}
      </span>
    </span>
  );
}
