import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

const ORIGIN = { Origin: "http://localhost:5173" };  // the api refuses cookie-authenticated writes from any other origin
const ROOT = path.resolve(import.meta.dirname, "../..");

test("after the replay and Stop: ready, every question answered, q_ip not used, toggled by hand survives reload", async ({ page }) => {
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

  // the Claude key from .env into the throwaway keyring (never printed), so General knowledge answers can happen
  const seeded = spawnSync("uv", helper("set-claude-key", "--from-env", ".env"), { cwd: ROOT, env, stdio: "ignore" });
  expect(seeded.status, "set-claude-key --from-env .env").toBe(0);

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
    // kept for diagnosing a missed question (classify timeouts, rules fallbacks, Ollama errors)
    mkdirSync(path.join(ROOT, "web/test-results"), { recursive: true });
    writeFileSync(path.join(ROOT, "web/test-results/helper-replay.log"), log.join(""));
    if (replay.pid) try { process.kill(-replay.pid); } catch { /* already gone */ }
  }

  await page.getByRole("button", { name: "Stop" }).click();
  await expect(page.getByText("Processing the recording…")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "Where Fleck helped" })).toBeVisible({ timeout: 240_000 });  // polled until ready

  // every question of the script has an assist (the one on the turn it ends in), and the page lists what the api holds
  const stored = await (await page.request.get(`/api/meetings/${meeting.id}`)).json();
  const questions = JSON.parse(readFileSync(path.join(ROOT, "fixtures/conversation/reference.json"), "utf8")).questions as { id: string; end_ms: number }[];
  const assistOf = (end: number) => stored.assists.find((a: { segment_id: string }) =>
    stored.segments.some((s: { segment_id: string; start_ms: number; end_ms: number }) => s.segment_id === a.segment_id && s.start_ms <= end && end <= s.end_ms));
  expect(questions.filter(q => !assistOf(q.end_ms)).map(q => q.id), "questions without an assist").toEqual([]);
  const items = page.getByRole("article");
  await expect(items).toHaveCount(stored.assists.length);
  await expect(page.getByRole("group", { name: "Recording timeline" }).getByRole("button")).toHaveCount(stored.assists.length);
  await expect(page.getByText("Claude cost $")).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.querySelector("audio")?.duration ?? 0), { timeout: 30_000 }).toBeGreaterThan(300);  // a real recording
  await page.screenshot({ path: "../docs/superpowers/screenshots/meeting-after.png", fullPage: true });

  const ip = page.getByRole("article", { name: assistOf(questions.find(q => q.id === "q_ip")!.end_ms).question });
  await expect(ip.getByRole("button", { name: "Not used, auto" })).toBeVisible();
  await ip.getByRole("button", { name: "Not used, auto" }).click();
  await expect(ip.getByRole("button", { name: "Used, by hand" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("article", { name: assistOf(questions.find(q => q.id === "q_ip")!.end_ms).question }).getByRole("button", { name: "Used, by hand" })).toBeVisible();

  await page.goto("/meetings");
  await expect(page.getByRole("link", { name: "Presales: after the call" }).first()).toBeVisible();
});
