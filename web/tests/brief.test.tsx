import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Brief from "../src/routes/Brief";

const rate = { doc_id: "d1", chunk_id: "c1", title: "Rate card FY2026", client: null, kind: "firm" };
let state: any;
let calls: { method: string; url: string; body?: string }[];

beforeEach(() => {
  calls = [];
  state = {
    event: { id: 5, title: "Presales: Harbourline Legal", start_at: "2026-10-31T11:00:00Z", end_at: "2026-10-31T11:45:00Z", client: { id: 3, name: "Harbourline Legal" } },
    status: "ready", context: "Harbourline Legal: 7 library documents (Rate card FY2026).", doc_count: 7, prepared_at: "2026-10-31T10:30:00Z", error: null, cost_usd: 0.012,
    items: [
      { topic: "delivery", question: "How fast can you start?", answer: "A pod within 2 weeks [1].", kind: "library", citations: [{ ...rate, title: "Delivery FAQ" }] },
      { topic: "commercial", question: "What do your people cost?", answer: "Senior engineer $5,200 a month offshore.", kind: "library", citations: [rate] },
      { topic: "proof", question: "Can you name a legal-sector reference?", answer: "", kind: "not_in_material", citations: [] },
    ],
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    if (url === "/api/events/5/brief") return new Response(JSON.stringify(init?.method === "POST" ? { ...state, status: "queued" } : state), { status: 200 });
    if (url === "/api/meetings") return new Response(JSON.stringify({ id: 9 }), { status: 200 });
    return new Response(null, { status: 404 });
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const open = async () => {
  render(<MemoryRouter initialEntries={["/events/5/brief"]}><Routes>
    <Route path="/events/:id/brief" element={<Brief />} /><Route path="/meetings/:id" element={<p>live page</p>} />
  </Routes></MemoryRouter>);
  await screen.findByRole("heading", { name: "Brief for Harbourline Legal" });
};

test("answered questions are cards with their source; gaps are listed apart", async () => {
  await open();
  expect(screen.getByText("Harbourline Legal: 7 library documents (Rate card FY2026).")).toBeTruthy();
  const likely = screen.getByRole("region", { name: "Likely questions" });
  const cards = within(likely).getAllByRole("article");
  expect(cards.map(c => within(c).getByRole("heading").textContent)).toEqual(["What do your people cost?", "How fast can you start?"]);  // by topic order
  expect(within(cards[0]).getByText("Commercial")).toBeTruthy();
  expect(within(cards[0]).getByText("Our material")).toBeTruthy();
  expect(within(cards[0]).getByText("Rate card FY2026")).toBeTruthy();
  expect(within(likely).getByText("2 answered from our material, 1 gap")).toBeTruthy();
  const thin = screen.getByRole("region", { name: "Where our material is thin" });
  expect(within(thin).getByText("Can you name a legal-sector reference?")).toBeTruthy();
  expect(within(likely).queryByText("Can you name a legal-sector reference?")).toBeNull();
});

test("Refresh brief queues a build and shows progress", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Refresh brief" }));
  await screen.findByText("Preparing the brief…");
  expect(calls.some(c => c.method === "POST" && c.url === "/api/events/5/brief")).toBe(true);
});

test("Record starts the helper and opens the live page", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Record" }));
  await screen.findByText("live page");
  expect(JSON.parse(calls.find(c => c.url === "/api/meetings")!.body!)).toEqual({ event_id: 5, start_helper: true });
});

test("no brief yet and a failed build both say so", async () => {
  state = { ...state, status: "failed", error: "RuntimeError: ANTHROPIC_API_KEY is empty", items: [] };
  await open();
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Couldn't build the brief"));
});

test("Record twice quickly starts one meeting", async () => {
  await open();
  const b = screen.getByRole("button", { name: "Record" });
  fireEvent.click(b);
  fireEvent.click(b);
  await screen.findByText("live page");
  expect(calls.filter(c => c.url === "/api/meetings").length).toBe(1);
});

test("a ready brief with no questions says so; repeated questions render", async () => {
  state = { ...state, items: [] };
  await open();
  expect(screen.getByText("No questions came back. Refresh brief.")).toBeTruthy();
  cleanup();
  const same = { topic: "risk", question: "Who owns the IP?", answer: "You do.", kind: "library", citations: [rate] };
  state = { ...state, items: [same, same] };
  await open();
  expect(screen.getAllByRole("article").length).toBe(2);
});

test("the prepared line counts the documents the answers cite, as for a brief with no client", async () => {
  state = { ...state, event: { ...state.event, client: null }, doc_count: 0,
            items: [...state.items, { topic: "delivery", question: "Who runs it?", answer: "A tech lead [1].", kind: "library", citations: [{ ...rate, doc_id: "d2", title: "Delivery FAQ" }] }] };
  render(<MemoryRouter initialEntries={["/events/5/brief"]}><Routes><Route path="/events/:id/brief" element={<Brief />} /></Routes></MemoryRouter>);
  expect((await screen.findByText(/Prepared/)).textContent).toMatch(/from 2 library documents\.$/);
});
