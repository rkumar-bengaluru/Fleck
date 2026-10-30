import { useState } from "react";
import { api, type CalendarEvent, type ClientRef } from "../api";

const NONE = "none", NEW = "new";

/** "Discovery: Orion Freight" -> "Orion Freight": the name offered as a new client for an unlinked external meeting. */
export function suggestedClient(title: string): string | null {
  const i = title.lastIndexOf(":");
  return i >= 0 && title.slice(i + 1).trim() ? title.slice(i + 1).trim() : null;
}

export default function ClientSelect({ event, clients, external, onChange }: {
  event: CalendarEvent; clients: ClientRef[]; external: boolean;
  onChange: (e: CalendarEvent, created?: ClientRef) => void;
}) {
  const [busy, setBusy] = useState(false);
  const id = `client-${event.id}`;
  const unset = !event.client && external;
  const suggestion = suggestedClient(event.title);
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
