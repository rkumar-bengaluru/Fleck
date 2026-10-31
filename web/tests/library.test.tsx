import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Library from "../src/routes/Library";

const doc = (id: number, o: object = {}) => ({ id, title: `Doc ${id}`, filename: `d${id}.md`, kind: "firm", client: null, client_id: null, status: "indexed", error: null,
  chunk_count: 5, used_count: 0, progress_done: 0, progress_total: 0, ...o });
let calls: { method: string; url: string }[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url });
    if (url === "/api/clients") return new Response(JSON.stringify([{ id: 1, name: "Calloway & Marsh" }]), { status: 200 });
    if (init?.method === "POST") return new Response(JSON.stringify(doc(9)), { status: 202 });
    return new Response(JSON.stringify([doc(1, { used_count: 14 }), doc(2, { kind: "client", client: "Calloway & Marsh", used_count: 0 }),
      doc(3, { status: "indexing", progress_done: 32, progress_total: 120 }), doc(4, { status: "failed", error: "Encrypted PDF" })]), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("rows show used count, kind label and indexing progress", async () => {
  render(<MemoryRouter><Library /></MemoryRouter>);
  const row = (t: string) => screen.getByText(t).closest("tr")!;
  await screen.findByText("Doc 1");
  expect(within(row("Doc 1")).getByText("14")).toBeTruthy();
  expect(within(row("Doc 2")).getByText("Our past work")).toBeTruthy();
  expect(within(row("Doc 2")).getByText("None yet")).toBeTruthy();
  expect(within(row("Doc 3")).getByText("Indexing, 32 of 120 passages")).toBeTruthy();
  expect(within(row("Doc 4")).getByText("Failed: Encrypted PDF")).toBeTruthy();
});

test("search and kind filter go to the api; upload needs a client for past work", async () => {
  render(<MemoryRouter><Library /></MemoryRouter>);
  await screen.findByText("Doc 1");
  fireEvent.change(screen.getByRole("combobox", { name: "Kind" }), { target: { value: "reference" } });
  await waitFor(() => expect(calls.some(c => c.url === "/api/library/documents?kind=reference")).toBe(true));
  fireEvent.change(screen.getByRole("searchbox", { name: "Search the library" }), { target: { value: "rate" } });
  await waitFor(() => expect(calls.some(c => c.url === "/api/library/documents?kind=reference&q=rate")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
  const dialog = await screen.findByRole("dialog", { name: "Add documents" });
  fireEvent.change(within(dialog).getByRole("combobox", { name: "Kind of document" }), { target: { value: "client" } });
  expect(within(dialog).getByRole("combobox", { name: "Client" })).toBeTruthy();
});
