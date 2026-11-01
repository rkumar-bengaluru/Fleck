export interface Attendee { email: string | null; name: string | null; self: boolean }
export interface ClientRef { id: number; name: string; reference_only?: boolean }
export interface CalendarStatus { connected: boolean; syncing: boolean }
export interface CalendarEvent {
  id: number;
  title: string;
  description: string | null;
  start_at: string;
  end_at: string;
  attendees: Attendee[];
  meeting_link: string | null;
  client: ClientRef | null;
  cancelled: boolean;  // gone from Google but kept: it had a client link, brief or meeting
  repeats?: "daily" | "weekly" | null;  // Upcoming only
}
export interface Device { id: number; name: string | null; last_seen: string | null }
export interface Me { id: number; email: string; name: string | null }

export class Unauthorized extends Error {}
export class HttpError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

// Any 401 (session expired mid-use too) goes to sign-in with a return path. An object so tests can replace it.
export const session = {
  signIn: (next: string) => window.location.assign(`/signin?next=${encodeURIComponent(next)}`),
};

/** A same-site path to return to after sign-in, or null (never another origin: "//evil.test" is rejected). */
export const safeNext = (next: string | null) => (next && next.startsWith("/") && !next.startsWith("//") ? next : null);

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const r = await fetch(path, {
    method: init?.method ?? "GET",
    credentials: "same-origin",
    headers: init?.body === undefined ? undefined : { "Content-Type": "application/json" },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (r.status === 401) {
    session.signIn(window.location.pathname + window.location.search);
    throw new Unauthorized();
  }
  if (!r.ok) throw new HttpError(`${init?.method ?? "GET"} ${path}: ${r.status}`, r.status);
  return (r.status === 204 ? undefined : await r.json()) as T;
}

export const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

export const domain = (email: string | null) => email?.split("@")[1]?.toLowerCase() ?? "";

export const setTitle = (page: string) => { document.title = `Fleck: ${page}`; };
