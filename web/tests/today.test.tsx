import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { CalendarEvent } from "../src/api";
import Today, { whoLine } from "../src/routes/Today";

const me = { id: 1, email: "rupak@northbeam.test", name: "Rupak K" };
const at = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();
const demo: CalendarEvent = {
  id: 7, title: "Presales: demo prospect", description: null, start_at: at(26), end_at: at(71),
  meeting_link: "https://meet.google.com/abc-defg-hij", client: null,
  attendees: [
    { email: "rupak@northbeam.test", name: "Rupak K", self: true },
    { email: "dana.whitfield@harbourline.test", name: "Dana Whitfield", self: false },
    { email: "marcus.lee@harbourline.test", name: "Marcus Lee", self: false },
    { email: "priya@northbeam.test", name: "Priya S", self: false },
  ],
};
const pricing: CalendarEvent = {
  id: 8, title: "Pricing review (internal)", description: null, start_at: at(200), end_at: at(230), meeting_link: null,
  client: null, attendees: [{ email: "priya@northbeam.test", name: "Priya S", self: false }],
};
const orion: CalendarEvent = { ...demo, id: 9, title: "Discovery: Orion Freight", start_at: at(1500), end_at: at(1545),
  client: { id: 3, name: "Harbourline Legal" } };

let calls: { method: string; url: string; body?: string }[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 });
    if (url.startsWith("/api/events?range=today")) return json([demo, pricing]);
    if (url.startsWith("/api/events?range=upcoming")) return json([orion]);
    if (url === "/api/clients") return json([{ id: 3, name: "Harbourline Legal", slug: "harbourline-legal" }]);
    if (url === "/api/devices") return json([{ id: 1, name: "Mac", last_seen: new Date().toISOString() }]);
    if (url === "/api/events/7/client") return json({ ...demo, client: { id: 3, name: "Harbourline Legal" } });
    return new Response("{}", { status: 404 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("renders today's events from /api/events", async () => {
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Presales: demo prospect" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Pricing review (internal)" })).toBeTruthy();
  expect(screen.getByText(/2 meetings from your Google Calendar\./)).toBeTruthy();
  expect(screen.getByText("Dana Whitfield, Marcus Lee (harbourline.test), Priya S. Google Meet.")).toBeTruthy();
  expect(screen.getByText("Priya S. No meeting link.")).toBeTruthy();
  expect(screen.getByText(/Starts in 2[56] min/)).toBeTruthy();
  expect(screen.getAllByRole("link", { name: "Open brief" })).toHaveLength(1);  // the next meeting only, as in the mockup
  expect(screen.getAllByRole("button", { name: "Record" })).toHaveLength(1);
  expect(await screen.findByText("Desktop app connected")).toBeTruthy();
  expect(screen.getByText("Discovery: Orion Freight")).toBeTruthy();  // Upcoming
  const tz = encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone);
  expect(calls.some(c => c.url === `/api/events?range=today&tz=${tz}`)).toBe(true);
});

test("client select calls PUT /api/events/{id}/client", async () => {
  render(<MemoryRouter><Today me={me} /></MemoryRouter>);
  const select = (await screen.findAllByLabelText("Client"))[0] as HTMLSelectElement;
  await waitFor(() => expect(select.querySelectorAll("option").length).toBe(3));  // Choose a client, New client: demo prospect, Harbourline Legal
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
  const select = (await screen.findAllByLabelText("Client"))[1] as HTMLSelectElement;
  expect(select.selectedOptions[0].textContent).toBe("No client");
  expect(whoLine({ ...pricing, attendees: [], meeting_link: "https://zoom.us/j/1" }, "northbeam.test")).toBe("Zoom.");
});
