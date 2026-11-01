import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, setTitle } from "../api";

interface Row { id: number; title: string; status: string; client_name: string | null; started_at: string }
const STATUS: Record<string, string> = { recording: "Recording", processing: "Processing", ready: "Ready", failed: "Failed" };

export default function Meetings() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { setTitle("Meetings"); api<Row[]>("/api/meetings").then(setRows).catch(() => setError(true)); }, []);
  return (
    <>
      <div className="page-head"><h1>Meetings</h1></div>
      {error && <p className="error" role="alert">Couldn't load the meetings.</p>}
      {rows?.length === 0 && <p className="muted">No meetings yet.</p>}
      {rows && rows.length > 0 && (
        <ul className="upcoming-list">
          {rows.map(r => (
            <li key={r.id} className="upcoming-row">
              <span className="upcoming-when">{new Date(r.started_at).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
              <Link className="upcoming-title" to={`/meetings/${r.id}`}>{r.title}</Link>
              <span className="upcoming-client">{r.client_name ?? ""}</span>
              <span className="upcoming-note">{STATUS[r.status] ?? r.status}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
