import { expect, test } from "@playwright/test";

// The demo journey (plan Task 23). Needs `make seed-demo` on the running stack: the library indexed, the event
// "Presales: demo prospect" linked to Harbourline Legal with its brief, and its replayed meeting ready.
const TITLE = "Presales: demo prospect";
const LIBRARY_DOCS = 15;  // every file under clients/ (README.md aside), all indexed by `make seed-library`

test("sign in, Today, brief, meeting notes and recording, chat with a transcript citation, library indexed", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/signin?next=%2F");
  await page.getByRole("button", { name: "Sign in with Google" }).click();
  await page.getByRole("button", { name: "rupak@northbeam.test" }).click();
  await page.getByRole("button", { name: "Allow" }).click();
  await expect(page.getByRole("heading", { name: "Today", level: 1 })).toBeVisible();

  const card = page.getByRole("article", { name: TITLE });
  await expect(card.getByLabel("Client").locator("option:checked")).toHaveText("Harbourline Legal");
  await card.getByRole("link", { name: "Open brief" }).click();
  await expect(page.getByRole("heading", { name: "Brief for Harbourline Legal", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What we know going in" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Likely questions|Where our material is thin/ }).first()).toBeVisible();
  if (process.env.FLECK_SCREENSHOTS) await page.screenshot({ path: "../docs/superpowers/screenshots/brief.png", fullPage: true });  // the committed one stays put

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Meetings", exact: true }).click();
  await page.getByRole("link", { name: TITLE }).first().click();
  await expect(page.getByRole("heading", { name: TITLE, level: 1 })).toBeVisible();
  const id = page.url().match(/\/meetings\/(\d+)/)![1];
  const stored = await (await page.request.get(`/api/meetings/${id}`)).json();
  expect(stored.meeting.status).toBe("ready");
  expect(stored.assists.length).toBeGreaterThanOrEqual(8);  // the script's 8 questions

  // Notes: every helped item, and the recording plays
  await expect(page.getByRole("heading", { name: "Where Fleck helped" })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(stored.assists.length);
  await page.getByRole("button", { name: "Play recording" }).click();
  await expect(page.getByRole("button", { name: "Pause recording" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.querySelector("audio")?.currentTime ?? 0), { timeout: 15_000 }).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Pause recording" }).click();

  // Chat answers from the transcript, with a timestamped citation that opens the line
  await page.getByRole("tab", { name: "Chat" }).click();
  await page.getByRole("textbox", { name: "Ask about this meeting" }).fill("What did they say about where documents can live?");
  await page.getByRole("button", { name: "Ask" }).click();
  const transcriptCite = page.getByRole("list", { name: "Sources" }).last().getByRole("link", { name: new RegExp(`^${TITLE} \\d+:\\d\\d$`) }).first();
  await expect(transcriptCite).toBeVisible({ timeout: 90_000 });
  await transcriptCite.click();
  await expect(page).toHaveURL(new RegExp(`/meetings/${id}\\?t=\\d+`));
  await expect(page.getByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");

  // Library: every seeded document indexed
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Library" }).click();
  await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
  const rows = page.locator("tbody tr");
  await expect(rows).toHaveCount(LIBRARY_DOCS);
  await expect(page.locator("tbody td.status-indexed")).toHaveCount(LIBRARY_DOCS);
});
