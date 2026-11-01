import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import Settings from "../src/routes/Settings";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

// The pair command's error says "Get a new one in Settings", so Settings must hand out the code (Task 23b walk-through).
test("Settings gets a pairing code and shows the command that uses it", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    return new Response(JSON.stringify({ code: "ABCD-1234" }), { status: 200 });
  }));
  render(<Settings me={{ id: 1, email: "rupak@northbeam.test", name: "Rupak K" }} />);
  fireEvent.click(screen.getByRole("button", { name: "Get a pairing code" }));
  expect(await screen.findByText("uv run --package fleck-helper fleck-helper pair ABCD-1234")).toBeTruthy();
  expect(calls).toEqual(["POST /api/devices/pairing-code"]);
});
