import { useEffect, useReducer } from "react";
import { api, Unauthorized, type Attendee } from "../api";
import type { AssistView } from "../components/AssistCard";
import type { Seg } from "../components/Transcript";
import type { AssistEvent, Citation, FleckEvent } from "../events";

export interface MeetingInfo { id: number; title: string; status: string; started_at: string; attendees: Attendee[] }
interface Snapshot {
  meeting: MeetingInfo; notes: string;
  segments: Omit<Seg, "final">[];
  assists: { assist_id: string; segment_id: string; question: string; answer: string; kind: AssistView["kind"]; citations: Citation[]; first_words_ms: number | null; late?: boolean }[];
}
interface Card extends AssistView { sentences: Record<number, string>; delta: Record<number, string> }
export interface State { meeting: MeetingInfo | null; notes: string | null; status: string; segments: Seg[]; assists: Card[]; error: boolean }

type Action = { snap: Snapshot } | { ev: FleckEvent } | { error: true };

const shown = (c: Card) => Object.keys({ ...c.delta, ...c.sentences }).map(Number).sort((a, b) => a - b)
  .map(i => c.sentences[i] ?? c.delta[i]).filter(Boolean).join(" ");

function applyAssist(cards: Card[], e: AssistEvent): Card[] {
  const old = cards.find(c => c.assist_id === e.assist_id);
  const base: Card = old ?? { assist_id: e.assist_id, segment_id: e.segment_id, question: e.question, text: "", kind: null, citations: [], first_words_ms: null, sentences: {}, delta: {} };
  let c = { ...base };
  if (e.phase === "thinking") c = { ...c, segment_id: e.segment_id, question: e.question, text: "", kind: null, citations: [], first_words_ms: null, late: e.late, sentences: {}, delta: {} };
  else if (e.phase === "delta" && e.sentence_index !== null) c.delta = { ...c.delta, [e.sentence_index]: (c.delta[e.sentence_index] ?? "") + e.text };
  else if (e.phase === "sentence" && e.sentence_index !== null) {
    c.sentences = { ...c.sentences, [e.sentence_index]: e.text };
    c.kind = e.kind ?? c.kind;
    if (e.citations.length) c.citations = e.citations;
  } else if (e.phase === "done") {
    c = { ...c, kind: e.kind ?? "not_in_material", citations: e.citations, first_words_ms: e.first_words_ms };
    c.text = e.text;
    return old ? cards.map(x => (x === old ? c : x)) : [...cards, c];
  }
  if (e.phase !== "thinking") c.text = shown(c);
  return old ? cards.map(x => (x === old ? c : x)) : [...cards, c];
}

export function reduce(s: State, a: Action): State {
  if ("error" in a) return { ...s, error: true };
  if ("snap" in a) {
    const { meeting, notes, segments, assists } = a.snap;
    const finals = new Set(segments.map(x => x.segment_id));
    const keep = s.segments.filter(x => !finals.has(x.segment_id));  // the snapshot's finals replace any partial of the same segment
    const merged = [...segments.map(x => ({ ...x, final: true })), ...keep].sort((x, y) => x.start_ms - y.start_ms);
    const ids = new Set(assists.map(x => x.assist_id));
    const live = s.assists.filter(x => !ids.has(x.assist_id));
    // a stored card still thinking (kind null) must not blank one already streaming on the socket
    const cards: Card[] = [...assists.map(x => (x.kind === null && s.assists.find(c => c.assist_id === x.assist_id)) || ({ assist_id: x.assist_id,
      segment_id: x.segment_id, question: x.question, text: x.answer, kind: x.kind, citations: x.citations, first_words_ms: x.first_words_ms, late: x.late,
      sentences: {}, delta: {} })), ...live];
    return { meeting, notes: s.notes ?? notes, status: meeting.status, segments: merged, assists: cards, error: false };
  }
  const e = a.ev;
  if (e.type === "meeting_state") return { ...s, status: e.status };
  if (e.type === "assist") return { ...s, assists: applyAssist(s.assists, e) };
  if (!e.final && s.segments.some(x => x.segment_id === e.segment_id && x.final)) return s;  // never a partial after its final
  const rest = s.segments.filter(x => x.segment_id !== e.segment_id);
  return { ...s, segments: [...rest, e].sort((x, y) => x.start_ms - y.start_ms) };
}

const RECONNECT_MS = 1000;
const wsUrl = (id: string) => `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/api/ws/meetings/${id}`;

/** The meeting's record plus the live socket. On every (re)connect: GET the record first, then apply the events that arrived meanwhile. */
export function useMeetingStream(id: string, off = false): State {
  const [state, dispatch] = useReducer(reduce, { meeting: null, notes: null, status: "recording", segments: [], assists: [], error: false });
  useEffect(() => {
    if (off) return;  // the meeting has ended: no socket
    let closed = false;
    let ws: WebSocket | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      const queued: FleckEvent[] = [];
      let ready = false;
      let stale = false;  // this connection is gone: its GET must not overwrite a newer snapshot
      const load = (attempt: number) => {
        api<Snapshot>(`/api/meetings/${id}`).then(snap => {
          if (closed || stale) return;
          dispatch({ snap });
          ready = true;
          queued.forEach(ev => dispatch({ ev }));
        }).catch(e => {
          if (e instanceof Unauthorized || closed || stale) return;
          dispatch({ error: true });  // the page shows it only before the first snapshot
          setTimeout(() => load(attempt + 1), Math.min(1000 * 2 ** attempt, 10_000));
        });
      };
      ws = new WebSocket(wsUrl(id));
      ws.onopen = () => load(0);
      ws.onmessage = m => {
        let ev: FleckEvent;
        try { ev = JSON.parse(m.data); } catch { return; }
        if (ready) dispatch({ ev }); else queued.push(ev);
      };
      ws.onclose = () => { stale = true; if (!closed) retry = setTimeout(connect, RECONNECT_MS); };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); ws?.close(); };
  }, [id, off]);
  return state;
}
