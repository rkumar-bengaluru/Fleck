import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AssistEvent, SegmentEvent } from "../src/events";
import { reduce, type State } from "../src/hooks/useMeetingStream";
import MeetingLive from "../src/routes/MeetingLive";

class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) { FakeSocket.all.push(this); setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
  emit(ev: object) { act(() => this.onmessage?.({ data: JSON.stringify(ev) })); }
}
const sock = () => FakeSocket.all[FakeSocket.all.length - 1];

const seg = (id: string, stream: "you" | "client", start_ms: number, text: string, final = true): SegmentEvent =>
  ({ type: "segment", meeting_id: "5", segment_id: id, stream, start_ms, end_ms: start_ms + 2000, text, final, gate: null });
const assist = (o: Partial<AssistEvent>): AssistEvent => ({ type: "assist", meeting_id: "5", assist_id: "a1", segment_id: "s2",
  phase: "thinking", question: "What does a senior engineer cost?", text: "", sentence_index: null, kind: null, from_brief: false,
  citations: [], first_words_ms: null, full_ms: null, rejected: [], llm: [], ...o });
const rate = { doc_id: "d1", chunk_id: "c1", title: "Rate card FY2026", client: null, kind: "firm" as const };

let state: { meeting: object; segments: object[]; assists: object[]; notes: string };
let calls: { method: string; url: string; body?: string }[];

beforeEach(() => {
  FakeSocket.all = [];
  calls = [];
  state = { meeting: { id: 5, title: "Presales: demo prospect", status: "recording", started_at: new Date(Date.now() - 83_000).toISOString(),
    attendees: [{ email: "dana@h.test", name: "Dana Whitfield", self: false }] }, segments: [], assists: [], notes: "" };
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    if (url === "/api/meetings/5") return new Response(JSON.stringify(state), { status: 200 });
    return new Response(null, { status: 204 });
  }));
  window.HTMLElement.prototype.scrollTo = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const open = async () => {
  render(<MemoryRouter initialEntries={["/meetings/5"]}><Routes><Route path="/meetings/:id" element={<MeetingLive />} /></Routes></MemoryRouter>);
  await screen.findByRole("heading", { name: "Presales: demo prospect" });
  await waitFor(() => expect(FakeSocket.all.length).toBe(1));
  await waitFor(() => expect(calls.filter(c => c.url === "/api/meetings/5").length).toBe(1));
  return sock();
};

test("test_applies_events_in_order", async () => {
  const s = await open();
  expect(sock().url).toMatch(/^ws:\/\/.*\/api\/ws\/meetings\/5$/);
  s.emit({ type: "meeting_state", meeting_id: "5", status: "recording" });
  s.emit(seg("s1", "client", 0, "We had a generic tool last year."));
  s.emit(seg("s2", "you", 5000, "That is common."));
  s.emit(seg("s3", "client", 72_000, "And are they dedicated"  , false));
  const items = within(screen.getByRole("region", { name: "Live transcript" })).getAllByRole("listitem");
  expect(items.map(i => i.textContent)).toEqual([
    expect.stringContaining("We had a generic tool last year."), expect.stringContaining("That is common."), expect.stringContaining("And are they dedicated")]);
  expect(items[0].textContent).toContain("0:00");
  expect(items[0].textContent).toContain("Client");
  expect(items[1].textContent).toContain("You");
  expect(screen.getByText("Dana Whitfield")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
});

test("test_partial_replaced_by_final", async () => {
  const s = await open();
  s.emit(seg("s1", "client", 0, "What does a senior", false));
  const partial = screen.getByText("What does a senior");
  expect(partial.className).toBe("partial");  // grey italics in theme.css
  s.emit(seg("s1", "client", 0, "What does a senior engineer cost per month?"));
  expect(screen.queryByText("What does a senior")).toBeNull();
  expect(screen.getAllByText("What does a senior engineer cost per month?")).toHaveLength(1);
  s.emit(seg("s1", "client", 0, "late partial", false));  // never after its final
  expect(screen.queryByText("late partial")).toBeNull();
});

test("test_reconnect_refetches_state", async () => {
  const s = await open();
  s.emit(seg("s1", "client", 0, "First line."));
  state.segments = [{ segment_id: "s1", stream: "client", start_ms: 0, end_ms: 1000, text: "First line.", gate: null },
                    { segment_id: "s9", stream: "you", start_ms: 9000, end_ms: 10000, text: "Missed while offline.", gate: null }];
  s.close();
  await waitFor(() => expect(FakeSocket.all.length).toBe(2), { timeout: 3_000 });
  expect(await screen.findByText("Missed while offline.", undefined, { timeout: 3_000 })).toBeTruthy();
  expect(calls.filter(c => c.url === "/api/meetings/5").length).toBe(2);
  expect(screen.getAllByText("First line.")).toHaveLength(1);
});

test("test_assist_renders_kind_labels", async () => {
  const s = await open();
  s.emit(seg("s2", "client", 1000, "What does a senior engineer cost?"));
  s.emit(assist({ phase: "thinking" }));
  expect(screen.getByText("Thinking…")).toBeTruthy();
  s.emit(assist({ phase: "sentence", sentence_index: 0, text: "$5,200 a month offshore.", citations: [rate] }));
  s.emit(assist({ phase: "done", kind: "library", text: "$5,200 a month offshore.", citations: [rate], first_words_ms: 2400 }));
  expect(screen.getByText("Our material")).toBeTruthy();
  expect(screen.getByText("Rate card FY2026")).toBeTruthy();
  expect(screen.getAllByText("$5,200 a month offshore.")).toHaveLength(2);  // the card and the polite announcement
  expect(screen.getByText("First words 2.4 s after the question")).toBeTruthy();
  const ref = { ...rate, title: "Orion MSA", kind: "reference" as const };
  s.emit(assist({ assist_id: "a2", phase: "done", kind: "reference", text: "Their rate was X.", citations: [ref] }));
  expect(screen.getByText("Reference contract")).toBeTruthy();
  expect(screen.getByText("Not our work: Orion MSA")).toBeTruthy();
  s.emit(assist({ assist_id: "a3", phase: "done", kind: "general", text: "Usually weeks." }));
  expect(screen.getByText("General knowledge")).toBeTruthy();
  expect(screen.getByText("Not from our material")).toBeTruthy();
  s.emit(assist({ assist_id: "a4", phase: "done", kind: "not_in_material", text: "" }));
  expect(screen.getAllByText("Not in our material")).toHaveLength(2);  // the chip and the announcement
});

test("test_notes_autosave_debounced", async () => {
  await open();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const box = screen.getByRole("textbox", { name: "My notes" });
    fireEvent.change(box, { target: { value: "- Dana owns" } });
    fireEvent.change(box, { target: { value: "- Dana owns the NDA backlog" } });
    expect(screen.getByText("Saving…")).toBeTruthy();
    await vi.advanceTimersByTimeAsync(900);
    expect(calls.some(c => c.method === "PUT")).toBe(false);
    await vi.advanceTimersByTimeAsync(200);
    const puts = calls.filter(c => c.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].url).toBe("/api/meetings/5/notes");
    expect(JSON.parse(puts[0].body!)).toEqual({ text: "- Dana owns the NDA backlog" });
    await waitFor(() => expect(screen.getByText("Saved")).toBeTruthy());
  } finally {
    vi.useRealTimers();
  }
});

test("a failed GET after the first snapshot keeps the page and retries; notes keep typing", async () => {
  const s = await open();
  fireEvent.change(screen.getByRole("textbox", { name: "My notes" }), { target: { value: "typed" } });
  const get = vi.mocked(fetch).getMockImplementation()! as (u: string, i?: RequestInit) => Promise<Response>;
  let fail = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => (fail && url === "/api/meetings/5" ? new Response("{}", { status: 500 }) : get(url, init))));
  s.close();
  await waitFor(() => expect(FakeSocket.all.length).toBe(2), { timeout: 3_000 });
  await new Promise(r => setTimeout(r, 100));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("heading", { name: "Presales: demo prospect" })).toBeTruthy();
  expect((screen.getByRole("textbox", { name: "My notes" }) as HTMLTextAreaElement).value).toBe("typed");
  fail = false;
  const gets = () => (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(c => c[0] === "/api/meetings/5").length;
  await waitFor(() => expect(gets()).toBeGreaterThan(1), { timeout: 3_000 });  // retried with backoff
});

test("the transcript is a focusable labelled region and announces finished answers only", async () => {
  const s = await open();
  const region = screen.getByRole("region", { name: "Live transcript" });
  expect(region.getAttribute("tabindex")).toBe("0");
  s.emit(seg("s2", "client", 1000, "What does a senior engineer cost?"));
  s.emit(assist({ phase: "thinking" }));
  const live = region.querySelector("[aria-live=polite]")!;
  expect(live.textContent).toBe("");
  s.emit(assist({ phase: "done", kind: "library", text: "$5,200 a month offshore.", citations: [rate] }));
  expect(live.textContent).toBe("$5,200 a month offshore.");
});

test("notes are saved when the editor unmounts with a change pending", async () => {
  await open();
  fireEvent.change(screen.getByRole("textbox", { name: "My notes" }), { target: { value: "late edit" } });
  cleanup();
  await waitFor(() => expect(calls.filter(c => c.method === "PUT").map(c => c.body)).toEqual([JSON.stringify({ text: "late edit" })]));
});

test("a failed Stop shows an inline error", async () => {
  await open();
  const get = vi.mocked(fetch).getMockImplementation()! as (u: string, i?: RequestInit) => Promise<Response>;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => (init?.method === "POST" ? new Response("{}", { status: 500 }) : get(url, init))));
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Couldn't stop the recording. Try again.");
});

test("auto-scroll follows new lines until the user scrolls up", async () => {
  const s = await open();
  const region = screen.getByRole("region", { name: "Live transcript" });
  const scrollTo = window.HTMLElement.prototype.scrollTo as ReturnType<typeof vi.fn>;
  Object.defineProperties(region, { scrollHeight: { value: 1000, configurable: true }, clientHeight: { value: 300, configurable: true },
    scrollTop: { value: 700, configurable: true, writable: true } });
  scrollTo.mockClear();
  s.emit(seg("s1", "client", 0, "one"));
  expect(scrollTo).toHaveBeenCalled();
  region.scrollTop = 100;
  fireEvent.scroll(region);
  scrollTo.mockClear();
  s.emit(seg("s2", "client", 2000, "two"));
  expect(scrollTo).not.toHaveBeenCalled();
});

const empty: State = { meeting: null, notes: null, status: "recording", segments: [], assists: [], error: false };
test("reducer: sentence removal, superseded card, and a thinking snapshot keeps a streaming card", () => {
  let st = reduce(empty, { ev: assist({ phase: "thinking" }) });
  st = reduce(st, { ev: assist({ phase: "sentence", sentence_index: 0, text: "Bad claim." }) });
  st = reduce(st, { ev: assist({ phase: "sentence", sentence_index: 1, text: "Fine." }) });
  expect(st.assists[0].text).toBe("Bad claim. Fine.");
  st = reduce(st, { ev: assist({ phase: "sentence", sentence_index: 0, text: "" }) });  // failed its checks: removed
  expect(st.assists[0].text).toBe("Fine.");
  const snap = { meeting: { id: 5, title: "t", status: "recording", started_at: "", attendees: [] }, notes: "", segments: [],
    assists: [{ assist_id: "a1", segment_id: "s2", question: "q", answer: "", kind: null, citations: [], first_words_ms: null }] };
  st = reduce(st, { snap });
  expect(st.assists[0].text).toBe("Fine.");
  st = reduce(st, { ev: assist({ phase: "thinking", question: "newer" }) });  // same id: superseded
  expect(st.assists).toHaveLength(1);
  expect(st.assists[0]).toMatchObject({ question: "newer", text: "", kind: null });
});
