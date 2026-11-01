import { useEffect, useState } from "react";
import { api, domain, Unauthorized, type CalendarEvent, type ClientRef } from "../api";

const NONE = "none", NEW = "new";

const PERSONAL = new Set(["gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "icloud", "me", "proton", "protonmail"]);
const GENERIC = new Set(["co", "com", "org", "net", "ac", "gov", "edu"]);

/** The guests' company from their email domain: harbourline.test -> "Harbourline", orion-freight.co.uk -> "Orion Freight". */
export function domainStem(event: Pick<CalendarEvent, "attendees">, myDomain: string): string | null {
  for (const a of event.attendees) {
    const labels = domain(a.email).split(".").slice(0, -1);  // drop the TLD
    if (labels.length > 1 && GENERIC.has(labels[labels.length - 1])) labels.pop();  // acme.co.uk
    const name = labels.pop();
    if (a.self || !name || domain(a.email) === myDomain || PERSONAL.has(name)) continue;
    return name.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  }
  return null;
}

/** Name for "New client: …": the title after its colon ("Discovery: Orion Freight"), else the domain stem. */
export function newClientName(event: Pick<CalendarEvent, "title" | "attendees">, myDomain: string): string | null {
  const i = event.title.lastIndexOf(":");
  const fromTitle = i >= 0 ? event.title.slice(i + 1).trim() : "";
  return fromTitle || domainStem(event, myDomain);
}

/** An existing client whose name starts with the guests' domain stem (harbourline -> Harbourline Legal). */
export function suggestedExisting(event: Pick<CalendarEvent, "attendees">, myDomain: string, clients: ClientRef[]): ClientRef | null {
  const stem = domainStem(event, myDomain)?.toLowerCase();
  return (stem && stem.length >= 3 && clients.find(c => c.name.toLowerCase().startsWith(stem))) || null;
}

export const isExternal = (e: Pick<CalendarEvent, "attendees">, myDomain: string) => e.attendees.some(a => !a.self && domain(a.email) !== myDomain);

type Linkable = Pick<CalendarEvent, "id" | "title" | "attendees" | "client">;

export default function ClientSelect<E extends Linkable>({ event, clients, myDomain, onChange, path }: {
  event: E; clients: ClientRef[] | null; myDomain: string;  // null while the client list loads
  onChange: (e: E, created?: ClientRef) => void;
  path?: string;  // where the PUT goes: the event's client by default, or a meeting's (which answers 204)
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const id = `client-${event.id}`;
  const unset = !event.client && isExternal(event, myDomain);
  // the linked client stays listed even if the list hides it (reference-only) or hasn't loaded
  const list = [...(clients ?? [])];
  if (event.client && !list.some(c => c.id === event.client!.id)) list.unshift(event.client);
  // Suggestions only once clients have loaded: never offer "New client" for a name that already exists.
  const suggested = unset && clients ? suggestedExisting(event, myDomain, clients) : null;
  const newName = unset && clients ? newClientName(event, myDomain) : null;
  const offerNew = newName && !list.some(c => c.name.toLowerCase() === newName.toLowerCase());

  async function choose(value: string) {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      let created: ClientRef | undefined;
      let clientId: number | null = value === NONE ? null : Number(value);
      if (value === NEW && newName) {
        created = await api<ClientRef>("/api/clients", { method: "POST", body: { name: newName } });
        clientId = created.id;
      }
      const saved = await api<E | undefined>(path ?? `/api/events/${event.id}/client`, { method: "PUT", body: { client_id: clientId } });
      onChange(saved ?? { ...event, client: clientId === null ? null : created ?? list.find(c => c.id === clientId) ?? null }, created);
    } catch {
      setFailed(true);  // the select snaps back to the saved value; a 401 has already gone to sign-in
    } finally {
      setBusy(false);
    }
  }

  const options = list.filter(c => c.id !== suggested?.id).map(c => <option key={c.id} value={String(c.id)}>{c.name}</option>);
  return (
    <>
      <label htmlFor={id}>Client</label>
      <select id={id} className={unset ? "client-select unset" : "client-select"} aria-busy={busy}
        value={event.client ? String(event.client.id) : unset ? "" : NONE} onChange={e => choose(e.target.value)}>
        {unset && <option value="" disabled>Choose a client</option>}
        {suggested && <option value={String(suggested.id)}>Suggested: {suggested.name}</option>}
        {!event.client && !unset && <option value={NONE}>No client</option>}
        {options}
        {event.client && <option value={NONE}>No client</option>}
        {offerNew && <option value={NEW}>New client: {newName}</option>}
      </select>
      {unset && !failed && <span className="hint">Link a client for a fuller brief.</span>}
      {failed && <span className="error" role="alert">Couldn't save the client. Try again.</span>}
    </>
  );
}

export function Failed({ what, retry }: { what: string; retry: () => void }) {
  return <p className="error" role="alert">Couldn't load your {what}.<button type="button" className="btn" onClick={retry}>Retry</button></p>;
}

/** Library clients: null while loading, so no "New client" is offered for a name that already exists. */
export function useClients() {
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

export interface MeetingRef { id: number; title: string; event_id: number | null; client_id: number | null; client_name: string | null }

/** Client of a meeting with no calendar event (an ad-hoc recording): the same select, saved on the meeting.
 * An event's meeting takes its client from the event (Today, brief), so callers render this only when event_id is null. */
export function MeetingClient({ meeting }: { meeting: MeetingRef }) {
  const [clients, addClient, error] = useClients();
  const [m, setM] = useState({ id: meeting.id, title: meeting.title, attendees: [],
    client: meeting.client_id === null ? null : { id: meeting.client_id, name: meeting.client_name ?? "" } } as Linkable);
  return (
    <div className="actions meeting-client">
      <ClientSelect event={m} clients={clients} myDomain="" path={`/api/meetings/${meeting.id}/client`} onChange={(e, c) => { addClient(c); setM(e); }} />
      {error}
    </div>
  );
}
