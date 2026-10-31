import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, setTitle } from "../api";
import NotesEditor from "../components/NotesEditor";
import Transcript from "../components/Transcript";
import { useMeetingStream } from "../hooks/useMeetingStream";

const clock = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

export default function MeetingLive() {
  const { id = "" } = useParams();
  const { meeting, notes, status, segments, assists, error } = useMeetingStream(id);
  const [now, setNow] = useState(Date.now());
  const [stopping, setStopping] = useState(false);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => { if (meeting) setTitle(meeting.title); }, [meeting?.title]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <p className="error" role="alert">Couldn't load this meeting.</p>;
  if (!meeting) return null;
  const guests = meeting.attendees.filter(a => !a.self).map(a => a.name || a.email?.split("@")[0] || "Guest");
  const recording = status === "recording";
  return (
    <>
      <header className="live-head">
        <div className="live-title">
          <h1>{meeting.title}</h1>
          {guests.length > 0 && <div className="subtitle">{guests.join(", ")}</div>}
        </div>
        {recording
          ? <div className="rec"><span className="rec-dot" />Recording {clock(now - new Date(meeting.started_at).getTime())}</div>
          : <div className="rec off">{status === "processing" ? "Processing" : "Meeting ended"}</div>}
        {recording && <button type="button" className="btn-stop" disabled={stopping}
          onClick={() => { setStopping(true); api(`/api/meetings/${id}/stop`, { method: "POST" }).catch(() => setStopping(false)); }}>Stop</button>}
      </header>
      <div className="live-grid">
        <NotesEditor meetingId={id} initial={notes ?? ""} />
        <section aria-labelledby="tx-h" className="live-card">
          <div className="live-card-head">
            <h2 id="tx-h">Live transcript</h2>
            <span className="live-hint">Answers also show in the panel on your screen</span>
          </div>
          <Transcript segments={segments} assists={assists} />
        </section>
      </div>
    </>
  );
}
