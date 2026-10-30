import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, browserZone, type CalendarEvent, type ClientRef, type Device, type Me } from "../api";
import ClientSelect from "../components/ClientSelect";

const CONNECTED_MS = 60_000;
const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const domain = (email: string | null) => email?.split("@")[1]?.toLowerCase() ?? "";

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

const isExternal = (e: CalendarEvent, myDomain: string) => e.attendees.some(a => !a.self && domain(a.email) !== myDomain);

export function MeetingCard({ event, clients, me, next, now, onChange }: {
  event: CalendarEvent; clients: ClientRef[]; me: Me; next: boolean; now: number;
  onChange: (e: CalendarEvent, created?: ClientRef) => void;
}) {
  const navigate = useNavigate();
  const myDomain = domain(me.email);
  const mins = Math.ceil((new Date(event.start_at).getTime() - now) / 60_000);
  return (
    <article className="meeting" aria-label={event.title}>
      <div className="when">
        <div className="when-start">{time(event.start_at)}</div>
        <div className="when-end">to {time(event.end_at)}</div>
      </div>
      <div className="what">
        <div className="what-head">
          <h2>{event.title}</h2>
          {next && mins > 0 && mins <= 60 && <span className="starts">Starts in {mins} min</span>}
        </div>
        <div className="who">{whoLine(event, myDomain)}</div>
        <div className="actions">
          <ClientSelect event={event} clients={clients} external={isExternal(event, myDomain)} onChange={onChange} />
          {next && <>
            <span className="spacer" />
            <Link className="btn-primary" to={`/events/${event.id}/brief`}>Open brief</Link>
            {/* Task 16 wires Record to POST /api/meetings; until then it opens the event. */}
            <button type="button" className="btn" onClick={() => navigate(`/events/${event.id}`)}>Record</button>
          </>}
        </div>
      </div>
    </article>
  );
}

function useClients() {
  const [clients, setClients] = useState<ClientRef[]>([]);
  useEffect(() => { api<ClientRef[]>("/api/clients").then(setClients).catch(() => {}); }, []);
  const add = (c?: ClientRef) => c && setClients(cs => [...cs, c].sort((a, b) => a.name.localeCompare(b.name)));
  return [clients, add] as const;
}

export default function Today({ me }: { me: Me }) {
  const [today, setToday] = useState<CalendarEvent[] | null>(null);
  const [upcoming, setUpcoming] = useState<CalendarEvent[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [clients, addClient] = useClients();
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const tz = encodeURIComponent(browserZone());
    api<CalendarEvent[]>(`/api/events?range=today&tz=${tz}`).then(setToday).catch(() => setToday([]));
    api<CalendarEvent[]>(`/api/events?range=upcoming&tz=${tz}`).then(setUpcoming).catch(() => {});
    const poll = () => api<Device[]>("/api/devices").then(setDevices).catch(() => {});
    poll();
    const t = setInterval(() => { setNow(Date.now()); poll(); }, 30_000);
    return () => clearInterval(t);
  }, []);

  const connected = devices.some(d => d.last_seen && now - new Date(d.last_seen).getTime() < CONNECTED_MS);
  const nextId = today?.find(e => new Date(e.end_at).getTime() > now)?.id;
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
            {today === null ? "" : n ? `${n} meeting${n === 1 ? "" : "s"} from your Google Calendar.` : "No meetings from your Google Calendar."}
          </p>
        </div>
        <div className="pill"><span className={connected ? "pill-dot on" : "pill-dot"} />
          {connected ? "Desktop app connected" : "Desktop app not connected"}</div>
      </header>

      <section aria-label="Today's meetings" className="meetings">
        {today?.map(e => <MeetingCard key={e.id} event={e} clients={clients} me={me} next={e.id === nextId} now={now} onChange={replace} />)}
      </section>

      {upcoming.length > 0 && (
        <section aria-labelledby="upcoming" className="upcoming">
          <h2 id="upcoming">Upcoming</h2>
          <div className="upcoming-list">
            {upcoming.map(e => (
              <div key={e.id} className="upcoming-row">
                <span className="upcoming-when">{day(e.start_at)}, {time(e.start_at)}</span>
                <span className="upcoming-title">{e.title}</span>
                <span className="upcoming-client">{e.client?.name ?? "No client"}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

/** The event a meeting card links to (Record, Open brief) until Brief (Task 18) and recording (Task 16) arrive. */
export function EventPage({ me }: { me: Me }) {
  const { id } = useParams();
  const [event, setEvent] = useState<CalendarEvent | null>(null);
  const [clients, addClient] = useClients();
  useEffect(() => { api<CalendarEvent>(`/api/events/${id}`).then(setEvent).catch(() => {}); }, [id]);
  if (!event) return null;
  return (
    <>
      <h1>{event.title}</h1>
      <section className="meetings">
        <MeetingCard event={event} clients={clients} me={me} next={false} now={Date.now()}
          onChange={(e, c) => { addClient(c); setEvent(e); }} />
      </section>
    </>
  );
}
