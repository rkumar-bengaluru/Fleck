import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

const ORIGIN = { Origin: "http://localhost:5173" };  // the api refuses cookie-authenticated writes from any other origin
const ROOT = path.resolve(import.meta.dirname, "../..");

test("after the replay and Stop: ready, every stored answer listed, a used marker toggled by hand survives reload", async ({ page }) => {
  test.setTimeout(900_000);
  await page.goto("/signin?next=%2F");
  await page.getByRole("button", { name: "Sign in with Google" }).click();
  await page.getByRole("button", { name: "rupak@northbeam.test" }).click();
  await page.getByRole("button", { name: "Allow" }).click();
  await expect(page.getByRole("heading", { name: "Today", level: 1 })).toBeVisible();

  // a throwaway keyring file, never the real keychain
  const env = { ...process.env, FLECK_TEST_KEYRING: path.join(mkdtempSync(path.join(tmpdir(), "fleck-keyring-")), "keyring.json"),
    PYTHON_KEYRING_BACKEND: "file_keyring.FileKeyring", PYTHONPATH: path.join(import.meta.dirname, "support") };
  const helper = (...args: string[]) => ["run", "--package", "fleck-helper", "fleck-helper", ...args];
  const { code } = await (await page.request.post("/api/devices/pairing-code", { headers: ORIGIN })).json();
  const paired = spawnSync("uv", helper("pair", code), { cwd: ROOT, env });
  expect(paired.status, String(paired.stderr)).toBe(0);

  const meeting = await (await page.request.post("/api/meetings", { data: { title: "Presales: after the call" }, headers: ORIGIN })).json();
  await page.goto(`/meetings/${meeting.id}`);
  await expect(page.getByRole("heading", { name: "Presales: after the call", level: 1 })).toBeVisible();

  const replay = spawn("uv", helper("replay", "fixtures/conversation/conversation.wav", "--meeting", String(meeting.id)), { cwd: ROOT, env, detached: true });
  const log: string[] = [];
  replay.stderr.on("data", d => log.push(String(d)));
  try {
    const exited = new Promise<number | null>(res => replay.on("exit", res));
    expect(await exited, log.join("")).toBe(0);  // the whole WAV, its answers and its audio chunks are uploaded
  } finally {
    if (replay.pid) try { process.kill(-replay.pid); } catch { /* already gone */ }
  }

  await page.getByRole("button", { name: "Stop" }).click();
  await expect(page.getByText("Processing the recording…")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "Where Fleck helped" })).toBeVisible({ timeout: 240_000 });  // polled until ready

  // the live path found 5 to 7 of the 8 questions in the runs so far; the page must show exactly what the api holds
  const stored = (await (await page.request.get(`/api/meetings/${meeting.id}`)).json()).assists.length;
  expect(stored).toBeGreaterThanOrEqual(4);
  const items = page.getByRole("article");
  await expect(items).toHaveCount(stored);
  await expect(page.getByRole("group", { name: "Recording timeline" }).getByRole("button")).toHaveCount(stored);
  await expect(page.getByText("Claude cost $")).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.querySelector("audio")?.duration ?? 0), { timeout: 30_000 }).toBeGreaterThan(300);  // a real recording
  await page.screenshot({ path: "../docs/superpowers/screenshots/meeting-after.png", fullPage: true });

  const first = items.first();
  const question = await first.getAttribute("aria-label");
  await first.getByRole("button", { name: /^(Not used|Used), auto$/ }).click();
  await expect(first.getByRole("button", { name: /, by hand$/ })).toBeVisible();
  const label = await first.getByRole("button", { name: /, by hand$/ }).innerText();
  await page.reload();
  await expect(page.getByRole("article", { name: question! }).getByRole("button", { name: label })).toBeVisible();

  await page.goto("/meetings");
  await expect(page.getByRole("link", { name: "Presales: after the call" }).first()).toBeVisible();
});
