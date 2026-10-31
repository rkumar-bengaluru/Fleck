import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, setTitle } from "../api";
import MeetingAfter from "./MeetingAfter";
import { MeetingClient } from "../components/ClientSelect";
import NotesEditor from "../components/NotesEditor";
import Transcript from "../components/Transcript";
import { useMeetingStream } from "../hooks/useMeetingStream";

const clock = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

export default function MeetingLive() {
  const { id = "" } = useParams();
  const [ended, setEnded] = useState(false);
  const { meeting, notes, status, segments, assists, error } = useMeetingStream(id, ended);
  useEffect(() => { if (status !== "recording") setEnded(true); }, [status]);  // MeetingAfter takes over: close the socket
  const [now, setNow] = useState(Date.now());
  const [stopping, setStopping] = useState(false);
  const [stopFailed, setStopFailed] = useState(false);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => { if (meeting) setTitle(meeting.title); }, [meeting?.title]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (error && !meeting) return <p className="error" role="alert">Couldn't load this meeting.</p>;
  if (!meeting) return null;
  const guests = meeting.attendees.filter(a => !a.self).map(a => a.name || a.email?.split("@")[0] || "Guest");
  if (status !== "recording") return <MeetingAfter id={id} />;  // processing, ready or failed (Task 19)
  return (
    <>
      <header className="live-head">
        <div className="live-title">
          <h1>{meeting.title}</h1>
          {guests.length > 0 && <div className="subtitle">{guests.join(", ")}</div>}
        </div>
        <div className="rec"><span className="rec-dot" />Recording {clock(now - new Date(meeting.started_at).getTime())}</div>
        <button type="button" className="btn-stop" disabled={stopping}
          onClick={() => { setStopping(true); setStopFailed(false); api(`/api/meetings/${id}/stop`, { method: "POST" }).catch(() => { setStopping(false); setStopFailed(true); }); }}>Stop</button>
        {stopFailed && <span className="error" role="alert">Couldn't stop the recording. Try again.</span>}
      </header>
      {meeting.event_id === null && <MeetingClient meeting={meeting} />}
      <div className="live-grid">
        <NotesEditor meetingId={id} initial={notes ?? ""} />
        <section className="live-card">
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
