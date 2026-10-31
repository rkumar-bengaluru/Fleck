import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, browserZone, domain, HttpError, setTitle, Unauthorized, type CalendarEvent, type CalendarStatus, type ClientRef, type Device, type Me } from "../api";
import ClientSelect from "../components/ClientSelect";

const CONNECTED_MS = 60_000;
const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

function linkKind(url: string | null): string {
  if (!url) return "No meeting link.";
  if (url.includes("meet.google.com")) return "Google Meet.";
  if (url.includes("zoom.us")) return "Zoom.";
  if (url.includes("teams.")) return "Microsoft Teams.";
  if (url.includes("webex.com")) return "Webex.";
  return "Meeting link.";
}

/** "Dana Whitfield, Marcus Lee (harbourline.test), Priya S. Google Meet." — guests grouped by domain, then colleagues. */
export function whoLine(e: CalendarEvent, myDomain: string): string {
  const others = e.attendees.filter(a => !a.self);
  const name = (a: CalendarEvent["attendees"][number]) => a.name || a.email?.split("@")[0] || "Guest";
  const groups = new Map<string, string[]>();
  for (const a of others.filter(a => domain(a.email) !== myDomain)) {
    groups.set(domain(a.email), [...(groups.get(domain(a.email)) ?? []), name(a)]);
  }
  const parts = [...groups].map(([d, names]) => `${names.join(", ")}${d ? ` (${d})` : ""}`);
  parts.push(...others.filter(a => domain(a.email) === myDomain).map(name));
  return parts.length ? `${parts.join(", ")}. ${linkKind(e.meeting_link)}` : linkKind(e.meeting_link);
}

export function MeetingCard({ event, clients, me, next, now, onChange }: {
  event: CalendarEvent; clients: ClientRef[] | null; me: Me; next: boolean; now: number;
  onChange: (e: CalendarEvent, created?: ClientRef) => void;
}) {
  const navigate = useNavigate();
  const [recordError, setRecordError] = useState<string | null>(null);
  const record = () => {
    setRecordError(null);
    api<{ id: number }>("/api/meetings", { method: "POST", body: { event_id: event.id, start_helper: true } })
      .then(m => navigate(`/meetings/${m.id}`))
      .catch(e => { if (!(e instanceof Unauthorized)) setRecordError(e instanceof HttpError && e.status === 409 ? "Desktop app not connected" : "Couldn't start the recording. Try again."); });
  };
  const myDomain = domain(me.email);
  const mins = Math.ceil((new Date(event.start_at).getTime() - now) / 60_000);
  const open = !event.cancelled && new Date(event.end_at).getTime() > now;
  return (
    <article className="meeting" aria-label={event.title}>
      <div className="when">
        <div className="when-start">{time(event.start_at)}</div>
        <div className="when-end">to {time(event.end_at)}</div>
      </div>
      <div className="what">
        <div className="what-head">
          <h2>{event.cancelled ? <s>{event.title}</s> : event.title}</h2>
          {event.cancelled && <span className="starts">Cancelled in Google Calendar</span>}
          {next && mins > 0 && mins <= 60 && <span className="starts">Starts in {mins} min</span>}
        </div>
        <div className="who">{whoLine(event, myDomain)}</div>
        <div className="actions">
          <ClientSelect event={event} clients={clients} myDomain={myDomain} onChange={onChange} />
          {open && <>
            <span className="spacer" />
            <Link className="btn-primary" to={`/events/${event.id}/brief`}>Open brief</Link>
            <button type="button" className="btn" onClick={record}>Record</button>
          </>}
        </div>
        {recordError && <p className="error" role="alert">{recordError}</p>}
      </div>
    </article>
  );
}

function Failed({ what, retry }: { what: string; retry: () => void }) {
  return <p className="error" role="alert">Couldn't load your {what}.<button type="button" className="btn" onClick={retry}>Retry</button></p>;
}

/** Library clients: null while loading, so no "New client" is offered for a name that already exists. */
function useClients() {
  const [clients, setClients] = useState<ClientRef[] | null>(null);
  const [failed, setFailed] = useState(false);
  const load = () => {
    setFailed(false);
    api<ClientRef[]>("/api/clients").then(cs => setClients(cs.filter(c => !c.reference_only))).catch(e => { if (!(e instanceof Unauthorized)) setFailed(true); });
  };
  useEffect(load, []);
  const add = (c?: ClientRef) => c && setClients(cs => [...(cs ?? []), c].sort((a, b) => a.name.localeCompare(b.name)));
  const error = failed ? <Failed what="clients" retry={load} /> : null;
  return [clients, add, error] as const;
}

export default function Today({ me }: { me: Me }) {
  const [today, setToday] = useState<CalendarEvent[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [status, setStatus] = useState<CalendarStatus | null>(null);
  const [upcoming, setUpcoming] = useState<CalendarEvent[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [clients, addClient, clientsError] = useClients();
  const [now, setNow] = useState(Date.now());

  const load = () => {
    const tz = encodeURIComponent(browserZone());
    setFailed(false);
    const fail = (e: unknown) => { if (!(e instanceof Unauthorized)) setFailed(true); };
    api<CalendarEvent[]>(`/api/events?range=today&tz=${tz}`).then(setToday).catch(fail);
    api<CalendarEvent[]>(`/api/events?range=upcoming&tz=${tz}`).then(setUpcoming).catch(fail);
  };
  useEffect(() => {
    setTitle("Today");
    const poll = () => {
      api<Device[]>("/api/devices").then(setDevices).catch(() => {});
      api<CalendarStatus>("/api/calendar/status").then(setStatus).catch(() => {});
    };
    load();
    poll();
    const t = setInterval(() => { setNow(Date.now()); load(); poll(); }, 30_000);  // picks up the worker's syncs
    return () => clearInterval(t);
  }, []);

  useEffect(() => {  // while a sync runs, check every 2 s and reload the day when it finishes
    if (!status?.syncing) return;
    const t = setTimeout(() => api<CalendarStatus>("/api/calendar/status").then(s => {
      setStatus(s);
      if (!s.syncing) load();
    }).catch(() => {}), 2_000);
    return () => clearTimeout(t);
  }, [status]);

  const connected = devices.some(d => d.last_seen && now - new Date(d.last_seen).getTime() < CONNECTED_MS);
  const nextId = today?.find(e => !e.cancelled && new Date(e.end_at).getTime() > now)?.id;
  const n = today?.length ?? 0;
  const replace = (e: CalendarEvent, created?: ClientRef) => {
    addClient(created);
    setToday(evs => evs && evs.map(x => (x.id === e.id ? e : x)));
  };
  const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Today</h1>
          <p className="subtitle">
            {new Date(now).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}.{" "}
            {today === null || failed ? "" : n ? `${n} meeting${n === 1 ? "" : "s"} from your Google Calendar.` : "No meetings from your Google Calendar."}
          </p>
          {status?.connected && status.syncing && <p className="subtitle" role="status">Syncing your calendar…</p>}
        </div>
        <div className="pill"><span className={connected ? "pill-dot on" : "pill-dot"} />
          {connected ? "Desktop app connected" : "Desktop app not connected"}</div>
      </header>

      {status && !status.connected && <p className="error" role="alert">Calendar not synced. <a href="/api/auth/login">Sign in again</a></p>}
      {failed && <Failed what="meetings" retry={load} />}
      {clientsError}
      <section aria-label="Today's meetings" className="meetings">
        {today?.map(e => <MeetingCard key={e.id} event={e} clients={clients} me={me} next={e.id === nextId} now={now} onChange={replace} />)}
      </section>

      {upcoming.length > 0 && (
        <section aria-labelledby="upcoming" className="upcoming">
          <h2 id="upcoming">Upcoming</h2>
          <div className="upcoming-list">
            {upcoming.map(e => (
              <Link key={e.id} className="upcoming-row" to={`/events/${e.id}`}>
                <span className="upcoming-when">{day(e.start_at)}, {time(e.start_at)}</span>
                <span className="upcoming-title">
                  {e.cancelled ? <><s>{e.title}</s> <span className="upcoming-note">Cancelled in Google Calendar</span></> : e.title}
                  {e.repeats && <span className="upcoming-note"> · {e.repeats}</span>}
                </span>
                <span className="upcoming-client">{e.client?.name ?? "No client"}</span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

/** The event a meeting card links to (Record, Open brief) until Brief (Task 18) arrives. */
export function EventPage({ me }: { me: Me }) {
  const { id } = useParams();
  const [event, setEvent] = useState<CalendarEvent | null>(null);
  const [clients, addClient, clientsError] = useClients();
  useEffect(() => { api<CalendarEvent>(`/api/events/${id}`).then(e => { setEvent(e); setTitle(e.title); }).catch(() => {}); }, [id]);
  if (!event) return null;
  return (
    <>
      <h1>{event.title}</h1>
      {clientsError}
      <section className="meetings">
        <MeetingCard event={event} clients={clients} me={me} next={false} now={Date.now()}
          onChange={(e, c) => { addClient(c); setEvent(e); }} />
      </section>
    </>
  );
}
