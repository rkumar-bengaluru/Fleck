import { useState, type RefObject } from "react";
import type { Kind } from "./SourceChip";

export const mmss = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
export const KIND_LABEL: Record<NonNullable<Kind>, string> = {
  library: "Our material", general: "General knowledge", not_in_material: "Not in our material", reference: "Reference contract",
};
export interface Marker { id: string; at_ms: number; kind: Kind }

/** The player for the stitched recording with one marker per assist, coloured by kind. Without a recording only the markers show. */
export default function RecordingTimeline({ src, audio, markers, totalMs, onSeek }: {
  src: string | null; audio: RefObject<HTMLAudioElement | null>; markers: Marker[]; totalMs: number; onSeek: (ms: number) => void;
}) {
  const [now, setNow] = useState(0);
  const [length, setLength] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [broken, setBroken] = useState(false);
  const total = length || totalMs || 1;
  const toggle = () => { const a = audio.current; if (a) void (a.paused ? a.play() : (a.pause(), undefined)); };
  return (
    <div className="player">
      {src && !broken && <audio ref={audio} src={src} preload="metadata" onError={() => setBroken(true)}
        onTimeUpdate={e => setNow(e.currentTarget.currentTime * 1000)} onLoadedMetadata={e => setLength(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration * 1000 : 0)}
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />}
      <button type="button" className="play" aria-label={playing ? "Pause recording" : "Play recording"} onClick={toggle} disabled={!src || broken}>
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">{playing ? <path d="M3 1.5h3v11H3zM8 1.5h3v11H8z" fill="currentColor" /> : <path d="M3 1.5v11l9-5.5z" fill="currentColor" />}</svg>
      </button>
      <span className="tl-time">{mmss(now)}</span>
      <div className="tl" role="group" aria-label="Recording timeline">
        <div className="tl-bar" />
        <div className="tl-played" style={{ width: `${Math.min(100, now / total * 100)}%` }} />
        {markers.map(m => (
          <button key={m.id} type="button" className={`tl-mark tl-${m.kind}`} style={{ left: `${Math.min(99, m.at_ms / total * 100)}%` }}
            aria-label={`Jump to ${mmss(m.at_ms)}, ${m.kind ? KIND_LABEL[m.kind] : "Thinking…"}`} onClick={() => onSeek(m.at_ms)} />
        ))}
      </div>
      <span className="tl-time end">{mmss(total)}</span>
      {(!src || broken) && <span className="tl-none">No recording</span>}
    </div>
  );
}
