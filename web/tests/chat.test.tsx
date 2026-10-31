import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import ChatPanel from "../src/components/ChatPanel";

const cites = [
  { type: "meeting", n: 1, meeting_id: 5, start_ms: 751_000, kind: "transcript", label: "Harbourline call 12:31" },
  { type: "document", n: 2, doc_id: "42", label: "Rate card FY2026" },
];
let calls: { method: string; url: string; body?: string }[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    if (init?.method === "POST") return new Response(JSON.stringify({ text: "Documents stay in iManage [1]. Senior engineers cost $5,200 [2].", citations: cites }), { status: 200 });
    return new Response(JSON.stringify([]), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const open = (meetingId: number | null) => render(<MemoryRouter><ChatPanel meetingId={meetingId} /></MemoryRouter>);

test("test_citation_links_to_transcript_time", async () => {
  open(5);
  fireEvent.change(screen.getByRole("textbox", { name: "Ask about this meeting" }), { target: { value: "Where can documents live?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  const link = await screen.findByRole("link", { name: "Harbourline call 12:31" });
  expect(link.getAttribute("href")).toBe("/meetings/5?t=751000");
  expect(screen.getByRole("link", { name: "Rate card FY2026" }).getAttribute("href")).toBe("/library?doc=42");
  expect(calls.find(c => c.method === "POST")).toMatchObject({ url: "/api/chat", body: JSON.stringify({ meeting_id: 5, text: "Where can documents live?" }) });
});

test("across meetings sends no meeting and shows saved history", async () => {
  vi.mocked(fetch).mockImplementation(async (url: any, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url });
    return new Response(JSON.stringify([{ role: "user", text: "Earlier?", citations: [] }, { role: "assistant", text: "Earlier answer.", citations: [] }]), { status: 200 });
  });
  open(null);
  await screen.findByText("Earlier answer.");
  expect(calls[0].url).toBe("/api/chat");
  expect(screen.getByRole("textbox", { name: "Ask across meetings" })).toBeTruthy();
});

test("a failed answer says so and keeps the question", async () => {
  vi.mocked(fetch).mockImplementation(async (_url: any, init?: RequestInit) =>
    init?.method === "POST" ? new Response("{}", { status: 502 }) : new Response("[]", { status: 200 }));
  open(5);
  fireEvent.change(screen.getByRole("textbox", { name: "Ask about this meeting" }), { target: { value: "Hello?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  await screen.findByRole("alert");
  await waitFor(() => expect((screen.getByRole("textbox", { name: "Ask about this meeting" }) as HTMLInputElement).value).toBe("Hello?"));
});

test("a failed history load has its own message", async () => {
  vi.mocked(fetch).mockImplementation(async () => new Response("{}", { status: 500 }));
  open(5);
  expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load the earlier messages.");
});
