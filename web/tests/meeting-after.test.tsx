import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import MeetingAfter from "../src/routes/MeetingAfter";

const rate = { doc_id: "d1", chunk_id: "c1", title: "Rate card FY2026", client: null, kind: "firm" };
const a = (n: number, o: object = {}) => ({ assist_id: `a${n}`, segment_id: `s${n}`, question: `Question ${n}?`, answer: `Answer ${n}.`, kind: "library",
  from_brief: false, citations: [rate], first_words_ms: 900, full_ms: 2000, rejected: [], used: true, used_by: "auto", ...o });

let state: any;
let calls: { method: string; url: string; body?: string }[];

beforeEach(() => {
  calls = [];
  state = {
    meeting: { id: 5, title: "Presales: Harbourline", status: "ready", failure_reason: null, started_at: "2026-10-30T11:00:00Z", ended_at: "2026-10-30T11:44:00Z",
      recorded_by_name: "Rupak K.", attendees: [] },
    segments: [1, 2, 3].map(n => ({ segment_id: `s${n}`, stream: n === 2 ? "client" : "you", start_ms: n * 60_000, end_ms: n * 60_000 + 3000, text: `Line ${n}`, gate: null })),
    assists: [a(1), a(2, { used: false, kind: "general" }), a(3, { used: false, used_by: "manual", kind: "not_in_material", citations: [] })],
    notes: "", cost_usd: 0.062,
    summary: [{ heading: "Key points", items: [{ user_text: "Dana owns the NDA backlog", ai_text: "and wants a first pass under an hour." }] }],
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    if (url === "/api/meetings/5") return new Response(JSON.stringify(state), { status: 200 });
    if (url.startsWith("/api/assists/")) return new Response(JSON.stringify({}), { status: 200 });
    return new Response(null, { status: 204 });
  }));
  window.HTMLMediaElement.prototype.play = vi.fn(async () => {});  // eslint-disable-line
  window.HTMLMediaElement.prototype.pause = vi.fn();
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const open = async () => {
  render(<MemoryRouter initialEntries={["/meetings/5"]}><Routes><Route path="/meetings/:id" element={<MeetingAfter id="5" />} /></Routes></MemoryRouter>);
  await screen.findByRole("heading", { name: "Presales: Harbourline" });
};

test("header shows the recorder, length and Claude cost", async () => {
  await open();
  expect(screen.getByText(/44 min\. Recorded by Rupak K\./)).toBeTruthy();
  expect(screen.getByText("Claude cost $0.06")).toBeTruthy();
});

test("test_used_toggle_patches_and_shows_by_hand", async () => {
  await open();
  const row = screen.getByRole("article", { name: "Question 2?" });
  fireEvent.click(within(row).getByRole("button", { name: "Not used, auto" }));
  await within(row).findByRole("button", { name: "Used, by hand" });
  expect(calls.find(c => c.url === "/api/assists/a2")).toMatchObject({ method: "PATCH", body: JSON.stringify({ used: true }) });
  expect(within(screen.getByRole("article", { name: "Question 1?" })).getByRole("button", { name: "Used, auto" })).toBeTruthy();
  expect(within(screen.getByRole("article", { name: "Question 3?" })).getByRole("button", { name: "Not used, by hand" })).toBeTruthy();
});

test("test_user_text_and_ai_text_styled_differently", async () => {
  await open();
  const mine = screen.getByText("Dana owns the NDA backlog");
  const claude = screen.getByText("and wants a first pass under an hour.");
  expect(mine.className).toBe("user-text");
  expect(claude.className).toBe("ai-text");
  expect(screen.getByText("Your words")).toBeTruthy();
  expect(screen.getByText("Added by Claude")).toBeTruthy();
});

test("test_timeline_marker_per_assist", async () => {
  await open();
  const tl = screen.getByRole("group", { name: "Recording timeline" });
  const marks = within(tl).getAllByRole("button");
  expect(marks.length).toBe(3);
  expect(marks[1].getAttribute("aria-label")).toBe("Jump to 2:00, General knowledge");
  expect(marks[1].className).toContain("tl-general");
});

test("the Transcript tab lists lines with clickable timestamps; Chat is the meeting chat", async () => {
  await open();
  fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
  expect(screen.getByText("Line 2")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Play from 2:00" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  expect(screen.getByRole("textbox", { name: "Ask about this meeting" })).toBeTruthy();
});

test("a capture outage shows as a marker line, not as the client's speech", async () => {
  state.segments.push({ segment_id: "gap-200000", stream: "client", start_ms: 200_000, end_ms: 215_000, text: "Audio from the call was interrupted (3:20–3:35)", gate: null });
  await open();
  fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
  const line = screen.getByText("Audio from the call was interrupted (3:20–3:35)").closest("li")!;
  expect(within(line).getByText("Fleck")).toBeTruthy();
  expect(within(line).queryByText("Client")).toBeNull();
});

test("processing polls every 3 s until ready", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  state.meeting.status = "processing"; state.summary = null;
  render(<MemoryRouter><MeetingAfter id="5" /></MemoryRouter>);
  await screen.findByText("Processing the recording…");
  state.meeting.status = "ready";
  await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
  await waitFor(() => expect(screen.queryByText("Processing the recording…")).toBeNull());
  const n = calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
  expect(calls.length).toBe(n);  // stopped polling
});

test("failed shows the reason and Try again re-enqueues", async () => {
  state.meeting.status = "failed"; state.meeting.failure_reason = "RuntimeError: ffmpeg failed";
  await open();
  expect(screen.getByText(/ffmpeg failed/)).toBeTruthy();
  state.meeting.status = "processing"; state.meeting.failure_reason = null;
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByText("Processing the recording…");
  expect(calls.some(c => c.method === "POST" && c.url === "/api/meetings/5/retry")).toBe(true);
});

test("a marker and a time link seek the audio and play", async () => {
  await open();
  const audio = document.querySelector("audio")!;
  fireEvent.click(screen.getByRole("button", { name: "Jump to 2:00, General knowledge" }));
  expect(audio.currentTime).toBe(120);
  expect(window.HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
  fireEvent.click(within(screen.getByRole("article", { name: "Question 3?" })).getByRole("button", { name: "Play from 3:00" }));
  expect(audio.currentTime).toBe(180);
  expect(window.HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
});

test("a play that is blocked does not throw", async () => {
  window.HTMLMediaElement.prototype.play = vi.fn(async () => { throw new Error("blocked"); });
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Jump to 1:00, Our material" }));
  await Promise.resolve();
});

test("with no recording the player, markers and time links are inert", async () => {
  await open();
  fireEvent.error(document.querySelector("audio")!);
  expect(await screen.findByText("No recording")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Play recording" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Jump to 1:00, Our material" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("button", { name: /^Play from/ })).toBeNull();
  expect(screen.getByText("1:00")).toBeTruthy();  // the time stays as text
});

test("a failed meeting has no seek links", async () => {
  state.meeting.status = "failed"; state.meeting.failure_reason = "boom";
  await open();
  expect(screen.queryByRole("button", { name: /^Play from/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Play recording" })).toBeNull();
});

test("polling stops when the page goes away", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  state.meeting.status = "processing"; state.summary = null;
  const { unmount } = render(<MemoryRouter><MeetingAfter id="5" /></MemoryRouter>);
  await screen.findByText("Processing the recording…");
  unmount();
  const n = calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(calls.length).toBe(n);
});

test("a failed save says so and shows the stored state", async () => {
  await open();
  const row = screen.getByRole("article", { name: "Question 2?" });
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url });
    if (url.startsWith("/api/assists/")) return new Response(null, { status: 500 });
    return new Response(JSON.stringify(state), { status: 200 });
  });
  fireEvent.click(within(row).getByRole("button", { name: "Not used, auto" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Couldn't save");
  await within(screen.getByRole("article", { name: "Question 2?" })).findByRole("button", { name: "Not used, auto" });
});

test("a failed retry says so", async () => {
  state.meeting.status = "failed"; state.meeting.failure_reason = "boom";
  await open();
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => (url.endsWith("/retry") ? new Response(null, { status: 500 }) : new Response(JSON.stringify(state), { status: 200 })));
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText(/Couldn't start processing again/)).toBeTruthy();
});

test("the views are tabs with a panel", async () => {
  await open();
  expect(screen.getByRole("tab", { name: "Notes" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("tabpanel")).toBeTruthy();
});

test("a late answer carries the Late tag after a reload", async () => {
  state.assists.push(a(4, { late: true }));
  await open();
  expect(within(screen.getByRole("article", { name: "Question 4?" })).getByText("Late")).toBeTruthy();
  expect(within(screen.getByRole("article", { name: "Question 1?" })).queryByText("Late")).toBeNull();
});

test("a chat citation link opens the transcript at that time and seeks the audio", async () => {
  render(<MemoryRouter initialEntries={["/meetings/5?t=120500"]}><Routes><Route path="/meetings/:id" element={<MeetingAfter id="5" />} /></Routes></MemoryRouter>);
  await screen.findByRole("heading", { name: "Presales: Harbourline" });
  await waitFor(() => expect(screen.getByRole("tab", { name: "Transcript" }).getAttribute("aria-selected")).toBe("true"));
  await waitFor(() => expect(document.querySelector("audio")!.currentTime).toBe(120.5));
  expect(screen.getByText("Line 2").closest("li")!.getAttribute("aria-current")).toBe("true");
});
