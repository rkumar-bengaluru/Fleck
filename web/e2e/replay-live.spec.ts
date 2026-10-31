import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

const ORIGIN = { Origin: "http://localhost:5173" };  // the api refuses cookie-authenticated writes from any other origin
const ROOT = path.resolve(import.meta.dirname, "../..");
const QUESTION = "What does a senior engineer cost per month";

test("replaying the conversation shows the rate-card answer within 10 s of the question", async ({ page }) => {
  test.setTimeout(360_000);
  const question = JSON.parse(readFileSync(path.join(ROOT, "fixtures/conversation/reference.json"), "utf8")).turns
    .find((t: { text: string }) => t.text.startsWith(QUESTION));

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

  const meeting = await (await page.request.post("/api/meetings", { data: { title: "Presales: demo prospect" }, headers: ORIGIN })).json();
  await page.goto(`/meetings/${meeting.id}`);
  await expect(page.getByRole("heading", { name: "Presales: demo prospect", level: 1 })).toBeVisible();

  const replay = spawn("uv", helper("replay", "fixtures/conversation/conversation.wav", "--meeting", String(meeting.id)), { cwd: ROOT, env, detached: true });  // own process group: uv's helper child dies with it
  const log: string[] = [];
  replay.stderr.on("data", d => log.push(String(d)));
  try {
    const transcript = page.getByRole("region", { name: "Live transcript" });
    await expect(transcript.getByRole("listitem").first()).toBeVisible({ timeout: 90_000 });
    const first = JSON.parse(readFileSync(path.join(ROOT, "fixtures/conversation/reference.json"), "utf8")).turns[0];
    const t0 = Date.now() - first.end_ms;  // the replay's clock: the first line was spoken to its end_ms

    const line = transcript.getByRole("listitem").filter({ hasText: QUESTION });
    await expect(line).toBeVisible({ timeout: 200_000 });
    const chip = line.getByText("Our material", { exact: true });
    const cited = line.getByText("Rate card FY2026");
    await expect(chip).toBeVisible({ timeout: 30_000 });
    await expect(cited).toBeVisible();
    const seconds = (Date.now() - (t0 + question.end_ms)) / 1000;
    console.log(`question end to chip: ${seconds.toFixed(1)} s`);
    expect(seconds).toBeLessThan(10);
    await page.screenshot({ path: "../docs/superpowers/screenshots/meeting-live.png", fullPage: true });
  } finally {
    if (replay.pid) process.kill(-replay.pid);
  }
});
