export interface Attendee { email: string | null; name: string | null; self: boolean }
export interface ClientRef { id: number; name: string }
export interface CalendarEvent {
  id: number;
  title: string;
  description: string | null;
  start_at: string;
  end_at: string;
  attendees: Attendee[];
  meeting_link: string | null;
  client: ClientRef | null;
}
export interface Device { id: number; name: string | null; last_seen: string | null }
export interface Me { id: number; email: string; name: string | null }

export class Unauthorized extends Error {}

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const r = await fetch(path, {
    method: init?.method ?? "GET",
    credentials: "same-origin",
    headers: init?.body === undefined ? undefined : { "Content-Type": "application/json" },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (r.status === 401) throw new Unauthorized();
  if (!r.ok) throw new Error(`${init?.method ?? "GET"} ${path}: ${r.status}`);
  return (r.status === 204 ? undefined : await r.json()) as T;
}

export const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
