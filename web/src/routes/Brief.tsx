import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, HttpError, setTitle, Unauthorized } from "../api";
import type { Citation } from "../events";
import SourceChip, { type Kind } from "../components/SourceChip";

const TOPICS = ["commercial", "proof", "delivery", "risk", "technical"];
const label = (t: string) => t[0].toUpperCase() + t.slice(1);
const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

interface BriefData {
  event: { id: number; title: string; start_at: string; end_at: string; client: { id: number; name: string } | null };
  status: "none" | "queued" | "building" | "ready" | "failed";
  context: string | null; doc_count: number; prepared_at: string | null; error: string | null; cost_usd: number;
  items: { topic: string; question: string; answer: string; kind: Kind; citations: Citation[] }[];
}

/** Pre-meeting brief, from docs/superpowers/mockups/WebBrief.dc.html. */
export default function Brief() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<BriefData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = (method = "GET") => api<BriefData>(`/api/events/${id}/brief`, { method }).then(d => { setData(d); setTitle(`Brief for ${d.event.client?.name ?? d.event.title}`); })
    .catch(e => { if (!(e instanceof Unauthorized)) setError("Couldn't load the brief."); });
  useEffect(() => { load(); }, [id]);
  const waiting = data?.status === "queued" || data?.status === "building";
  useEffect(() => {  // the worker is building it: look again every 3 s
    if (!waiting) return;
    const t = setInterval(() => load(), 3_000);
    return () => clearInterval(t);
  }, [waiting, id]);
  const [starting, setStarting] = useState(false);
  const record = () => { if (starting) return; setStarting(true); api<{ id: number }>("/api/meetings", { method: "POST", body: { event_id: Number(id), start_helper: true } })
    .then(m => navigate(`/meetings/${m.id}`))
    .catch(e => { if (!(e instanceof Unauthorized)) setError(e instanceof HttpError && e.status === 409 ? "Desktop app not connected" : "Couldn't start the recording. Try again."); setStarting(false); }); };

  if (!data) return error ? <p className="error" role="alert">{error}</p> : null;
  const answered = data.items.filter(i => i.kind !== "not_in_material")
    .sort((a, b) => TOPICS.indexOf(a.topic) - TOPICS.indexOf(b.topic));
  const thin = data.items.filter(i => i.kind === "not_in_material");
  const e = data.event;
  return (
    <>
      <Link to="/">Back to Today</Link>
      <header className="page-head">
        <div>
          <h1>Brief for {e.client?.name ?? e.title}</h1>
          <p className="subtitle">
            {e.title}, {time(e.start_at)} to {time(e.end_at)}.{data.prepared_at && ` Prepared ${time(data.prepared_at)} from ${data.doc_count} library document${data.doc_count === 1 ? "" : "s"}.`}
          </p>
        </div>
        <div className="actions">
          <button type="button" className="btn" disabled={waiting} onClick={() => { setError(null); load("POST"); }}>Refresh brief</button>
          <button type="button" className="btn-primary" onClick={record}>Record</button>
        </div>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {waiting && <p className="subtitle" role="status">Preparing the brief…</p>}
      {data.status === "none" && <p className="subtitle">No brief yet. Refresh brief to prepare one.</p>}
      {data.status === "ready" && data.items.length === 0 && <p className="subtitle">No questions came back. Refresh brief.</p>}
      {data.status === "failed" && <p className="error" role="alert">Couldn't build the brief. {data.error}</p>}

      {data.context && (
        <section aria-labelledby="ctx" className="brief-box">
          <h2 id="ctx">What we know going in</h2>
          <p>{data.context}</p>
        </section>
      )}

      {data.items.length > 0 && (
        <section aria-labelledby="likely" className="brief-likely">
          <div className="brief-head">
            <h2 id="likely">Likely questions</h2>
            <span className="subtitle">{answered.length} answered from our material, {thin.length} gap{thin.length === 1 ? "" : "s"}</span>
          </div>
          <div className="brief-grid">
            {answered.map((i, n) => (
              <article key={n} className="brief-card">
                <span className="brief-topic">{label(i.topic)}</span>
                <h3>{i.question}</h3>
                <p>{i.answer}</p>
                <SourceChip kind={i.kind} citations={i.citations} />
              </article>
            ))}
          </div>
        </section>
      )}

      {thin.length > 0 && (
        <section aria-labelledby="gaps" className="brief-box brief-thin">
          <h2 id="gaps">Where our material is thin</h2>
          {thin.map((i, n) => <div key={n}><b>{i.question}</b> Nothing in the library answers this.</div>)}
        </section>
      )}
    </>
  );
}
