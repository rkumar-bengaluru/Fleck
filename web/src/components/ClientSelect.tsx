import { useState } from "react";
import { api, domain, type CalendarEvent, type ClientRef } from "../api";

const NONE = "none", NEW = "new";

const PERSONAL = new Set(["gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "icloud", "me", "proton", "protonmail"]);
const GENERIC = new Set(["co", "com", "org", "net", "ac", "gov", "edu"]);

/** Name offered as a new client: the guests' company from their email domain (harbourline.test -> "Harbourline"),
 *  else the title after its colon ("Discovery: Orion Freight" -> "Orion Freight"). */
export function suggestedClient(event: CalendarEvent, myDomain: string): string | null {
  for (const a of event.attendees) {
    const labels = domain(a.email).split(".").slice(0, -1);  // drop the TLD
    if (labels.length > 1 && GENERIC.has(labels[labels.length - 1])) labels.pop();  // acme.co.uk
    const name = labels.pop();
    if (a.self || !name || domain(a.email) === myDomain || PERSONAL.has(name)) continue;
    return name.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  }
  const i = event.title.lastIndexOf(":");
  return i >= 0 && event.title.slice(i + 1).trim() ? event.title.slice(i + 1).trim() : null;
}

export const isExternal = (e: CalendarEvent, myDomain: string) => e.attendees.some(a => !a.self && domain(a.email) !== myDomain);

export default function ClientSelect({ event, clients, myDomain, onChange }: {
  event: CalendarEvent; clients: ClientRef[]; myDomain: string;
  onChange: (e: CalendarEvent, created?: ClientRef) => void;
}) {
  const [busy, setBusy] = useState(false);
  const id = `client-${event.id}`;
  const unset = !event.client && isExternal(event, myDomain);
  const suggestion = suggestedClient(event, myDomain);
  const offerNew = unset && suggestion && !clients.some(c => c.name.toLowerCase() === suggestion.toLowerCase());

  async function choose(value: string) {
    setBusy(true);
    try {
      let created: ClientRef | undefined;
      let clientId: number | null = value === NONE ? null : Number(value);
      if (value === NEW && suggestion) {
        created = await api<ClientRef>("/api/clients", { method: "POST", body: { name: suggestion } });
        clientId = created.id;
      }
      onChange(await api<CalendarEvent>(`/api/events/${event.id}/client`, { method: "PUT", body: { client_id: clientId } }), created);
    } finally {
      setBusy(false);
    }
  }

  const options = clients.map(c => <option key={c.id} value={String(c.id)}>{c.name}</option>);
  return (
    <>
      <label htmlFor={id}>Client</label>
      <select id={id} className={unset ? "client-select unset" : "client-select"} disabled={busy}
        value={event.client ? String(event.client.id) : unset ? "" : NONE} onChange={e => choose(e.target.value)}>
        {unset && <option value="" disabled>Choose a client</option>}
        {unset && offerNew && <option value={NEW}>New client: {suggestion}</option>}
        {!event.client && !unset && <option value={NONE}>No client</option>}
        {options}
        {event.client && <option value={NONE}>No client</option>}
      </select>
      {unset && <span className="hint">Link a client for a fuller brief.</span>}
    </>
  );
}
