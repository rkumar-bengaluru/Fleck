import { useState } from "react";
import { api, domain, type CalendarEvent, type ClientRef } from "../api";

const NONE = "none", NEW = "new";

const PERSONAL = new Set(["gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "icloud", "me", "proton", "protonmail"]);
const GENERIC = new Set(["co", "com", "org", "net", "ac", "gov", "edu"]);

/** The guests' company from their email domain: harbourline.test -> "Harbourline", orion-freight.co.uk -> "Orion Freight". */
export function domainStem(event: CalendarEvent, myDomain: string): string | null {
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
export function newClientName(event: CalendarEvent, myDomain: string): string | null {
  const i = event.title.lastIndexOf(":");
  const fromTitle = i >= 0 ? event.title.slice(i + 1).trim() : "";
  return fromTitle || domainStem(event, myDomain);
}

/** An existing client whose name starts with the guests' domain stem (harbourline -> Harbourline Legal). */
export function suggestedExisting(event: CalendarEvent, myDomain: string, clients: ClientRef[]): ClientRef | null {
  const stem = domainStem(event, myDomain)?.toLowerCase();
  return (stem && stem.length >= 3 && clients.find(c => c.name.toLowerCase().startsWith(stem))) || null;
}

export const isExternal = (e: CalendarEvent, myDomain: string) => e.attendees.some(a => !a.self && domain(a.email) !== myDomain);

export default function ClientSelect({ event, clients, myDomain, onChange }: {
  event: CalendarEvent; clients: ClientRef[] | null; myDomain: string;  // null while the client list loads
  onChange: (e: CalendarEvent, created?: ClientRef) => void;
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
      onChange(await api<CalendarEvent>(`/api/events/${event.id}/client`, { method: "PUT", body: { client_id: clientId } }), created);
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
