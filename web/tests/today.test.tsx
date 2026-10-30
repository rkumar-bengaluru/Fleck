import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { safeNext, session, type CalendarEvent } from "../src/api";
import { suggestedClient } from "../src/components/ClientSelect";
import Settings from "../src/routes/Settings";
import SignIn from "../src/routes/SignIn";
import Today, { whoLine } from "../src/routes/Today";

const me = { id: 1, email: "rupak@northbeam.test", name: "Rupak K" };
const at = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();
const demo: CalendarEvent = {
  id: 7, title: "Presales: demo prospect", description: null, start_at: at(26), end_at: at(71),
  meeting_link: "https://meet.google.com/abc-defg-hij", client: null, cancelled: false,
  attendees: [
    { email: "rupak@northbeam.test", name: "Rupak K", self: true },
    { email: "dana.whitfield@harbourline.test", name: "Dana Whitfield", self: false },
    { email: "marcus.lee@harbourline.test", name: "Marcus Lee", self: false },
    { email: "priya@northbeam.test", name: "Priya S", self: false },
  ],
};
const pricing: CalendarEvent = {
  id: 8, title: "Pricing review (internal)", description: null, start_at: at(200), end_at: at(230), meeting_link: null,
  client: null, cancelled: false, attendees: [{ email: "priya@northbeam.test", name: "Priya S", self: false }],
};
const ended: CalendarEvent = { ...pricing, id: 10, title: "Earlier sync", start_at: at(-90), end_at: at(-60) };
const dropped: CalendarEvent = { ...pricing, id: 11, title: "Dropped call", start_at: at(300), end_at: at(330), cancelled: true,
  client: { id: 3, name: "Harbourline Legal" } };
const standup: CalendarEvent = { ...pricing, id: 12, title: "Standup", start_at: at(1400), end_at: at(1415), repeats: "daily" };
const orion: CalendarEvent = { ...demo, id: 9, title: "Discovery: Orion Freight", start_at: at(1500), end_at: at(1545),
  client: { id: 3, name: "Harbourline Legal" } };

let calls: { method: string; url: string; body?: string }[];
// per-test overrides: url prefix -> response (or a promise of one)
let override: Record<string, () => Response | Promise<Response>>;

beforeEach(() => {
  calls = [];
  override = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    const hit = Object.keys(override).find(k => url.startsWith(k));
    if (hit) return override[hit]();
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 });
    if (url.startsWith("/api/events?range=today")) return json([ended, demo, pricing, dropped]);
    if (url.startsWith("/api/events?range=upcoming")) return json([standup, orion]);
    if (url === "/api/clients") return json([{ id: 3, name: "Harbourline Legal", slug: "harbourline-legal" }]);
    if (url === "/api/devices") return json([{ id: 1, name: "Mac", last_seen: new Date().toISOString() }]);
    if (url === "/api/events/7/client") return json({ ...demo, client: { id: 3, name: "Harbourline Legal" } });
    return new Response("{}", { status: 404 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("renders today's events from /api/events", async () => {
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Presales: demo prospect" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Pricing review (internal)" })).toBeTruthy();
  expect(screen.getByText(/4 meetings from your Google Calendar\./)).toBeTruthy();
  expect(screen.getByText("Dana Whitfield, Marcus Lee (harbourline.test), Priya S. Google Meet.")).toBeTruthy();
  expect(screen.getAllByText("Priya S. No meeting link.")).toHaveLength(3);
  expect(screen.getAllByText(/Starts in \d+ min/)).toHaveLength(1);  // the next meeting only
  expect(screen.getByText(/Starts in 2[56] min/)).toBeTruthy();
  // every meeting that hasn't ended and isn't cancelled: demo and pricing, not the earlier one or the dropped one
  expect(screen.getAllByRole("link", { name: "Open brief" }).map(a => a.getAttribute("href"))).toEqual(["/events/7/brief", "/events/8/brief"]);
  expect(screen.getAllByRole("button", { name: "Record" })).toHaveLength(2);
  expect(screen.getByText("Cancelled in Google Calendar")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Dropped call" }).querySelector("s")).toBeTruthy();  // struck through
  expect(screen.getByText("· daily", { exact: false })).toBeTruthy();  // Upcoming: the series once, with how often
  expect(await screen.findByText("Desktop app connected")).toBeTruthy();
  expect(screen.getByText("Discovery: Orion Freight")).toBeTruthy();  // Upcoming
  const tz = encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone);
  expect(calls.some(c => c.url === `/api/events?range=today&tz=${tz}`)).toBe(true);
});

test("client select calls PUT /api/events/{id}/client", async () => {
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  const select = (await screen.findAllByLabelText("Client"))[1] as HTMLSelectElement;
  await waitFor(() => expect(select.querySelectorAll("option").length).toBe(3));
  expect([...select.querySelectorAll("option")].map(o => o.textContent)).toEqual(["Choose a client", "New client: Harbourline", "Harbourline Legal"]);
  expect(screen.getByText("Link a client for a fuller brief.")).toBeTruthy();
  fireEvent.change(select, { target: { value: "3" } });
  await waitFor(() => expect(calls.some(c => c.method === "PUT")).toBe(true));
  const put = calls.find(c => c.method === "PUT")!;
  expect(put.url).toBe("/api/events/7/client");
  expect(JSON.parse(put.body!)).toEqual({ client_id: 3 });
  await waitFor(() => expect(select.value).toBe("3"));
  expect(screen.queryByText("Link a client for a fuller brief.")).toBeNull();
});

test("internal meeting without a client shows No client", async () => {
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  const select = (await screen.findAllByLabelText("Client"))[2] as HTMLSelectElement;
  expect(select.selectedOptions[0].textContent).toBe("No client");
  expect(whoLine({ ...pricing, attendees: [], meeting_link: "https://zoom.us/j/1" }, "northbeam.test")).toBe("Zoom.");
});

test("new-client suggestion comes from the guests' email domain, else the title", () => {
  const guest = (email: string) => ({ ...pricing, title: "Discovery: Orion Freight",
    attendees: [{ email: "rupak@northbeam.test", name: null, self: true }, { email, name: null, self: false }] });
  expect(suggestedClient(demo, "northbeam.test")).toBe("Harbourline");
  expect(suggestedClient(guest("sam@orion-freight.co.uk"), "northbeam.test")).toBe("Orion Freight");
  expect(suggestedClient(guest("sam@eu.acme.com"), "northbeam.test")).toBe("Acme");
  expect(suggestedClient(guest("sam@gmail.com"), "northbeam.test")).toBe("Orion Freight");  // personal mail: use the title
  expect(suggestedClient(pricing, "northbeam.test")).toBeNull();
});

test("a 401 anywhere sends the browser to sign-in with a return path, never the empty day", async () => {
  const signIn = vi.spyOn(session, "signIn").mockImplementation(() => {});
  override["/api/events"] = () => new Response("{}", { status: 401 });
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  await waitFor(() => expect(signIn).toHaveBeenCalledWith(window.location.pathname + window.location.search));
  expect(screen.queryByText(/No meetings/)).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(safeNext("/events/7")).toBe("/events/7");
  expect(safeNext("//evil.test")).toBeNull();
  expect(safeNext("https://evil.test")).toBeNull();
});

test("a failed load shows an inline error with Retry, never the empty day", async () => {
  override["/api/events?range=today"] = () => new Response("{}", { status: 500 });
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  expect((await screen.findByRole("alert")).textContent).toContain("Couldn't load your meetings.");
  expect(screen.queryByText(/No meetings/)).toBeNull();
  delete override["/api/events?range=today"];
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("heading", { name: "Presales: demo prospect" })).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(document.title).toBe("Fleck: Today");
});

test("a failed client save shows an error and keeps focus on the select", async () => {
  override["/api/events/7/client"] = () => new Response("{}", { status: 500 });
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  const select = (await screen.findAllByLabelText("Client"))[1] as HTMLSelectElement;
  await waitFor(() => expect(select.querySelectorAll("option").length).toBe(3));
  select.focus();
  fireEvent.change(select, { target: { value: "3" } });
  expect(select.getAttribute("aria-busy")).toBe("true");
  expect(select.disabled).toBe(false);
  expect((await screen.findByRole("alert")).textContent).toBe("Couldn't save the client. Try again.");
  expect(document.activeElement).toBe(select);
  expect(select.value).toBe("");  // back to the saved value
});

test("New client is offered only once clients have loaded; a failed client load is shown", async () => {
  let release!: (r: Response) => void;
  override["/api/clients"] = () => new Promise(r => { release = r; });
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  const select = (await screen.findAllByLabelText("Client"))[1] as HTMLSelectElement;
  expect([...select.querySelectorAll("option")].map(o => o.textContent)).toEqual(["Choose a client"]);
  release(new Response(JSON.stringify([{ id: 4, name: "Harbourline", slug: "harbourline" }]), { status: 200 }));
  await waitFor(() => expect([...select.querySelectorAll("option")].map(o => o.textContent)).toEqual(["Choose a client", "Harbourline"]));
  cleanup();
  override["/api/clients"] = () => new Response("{}", { status: 500 });
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  expect((await screen.findByRole("alert")).textContent).toContain("Couldn't load your clients.");
});

test("page titles and the sign-in heading", async () => {
  render(<MemoryRouter initialEntries={["/signin?next=/events/7"]}><SignIn nextKey="k" /></MemoryRouter>);
  expect(screen.getByRole("heading", { level: 1, name: "Fleck" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sign in with Google" })).toBeTruthy();
  expect(document.title).toBe("Fleck: Sign in");
  cleanup();
  render(<MemoryRouter><Settings me={me} /></MemoryRouter>);
  expect(document.title).toBe("Fleck: Settings");
});
