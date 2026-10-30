import { expect, test } from "@playwright/test";

const ORIGIN = { Origin: "http://localhost:5173" };  // the api refuses cookie-authenticated writes from any other origin

test("sign in through gsim, see Today, link a client and keep it after reload", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/signin\?next=%2F$/);
  await page.getByRole("button", { name: "Sign in with Google" }).click();
  await page.getByRole("button", { name: "rupak@northbeam.test" }).click();
  await page.getByRole("button", { name: "Allow" }).click();
  await expect(page.getByRole("heading", { name: "Today", level: 1 })).toBeVisible();

  const client = await (await page.request.post("/api/clients", { data: { name: "Harbourline Legal" }, headers: ORIGIN })).json();
  const card = page.getByRole("article", { name: "Presales: demo prospect" });
  await expect(async () => {  // the sign-in sync runs in the worker
    await page.reload();
    await expect(card).toBeVisible({ timeout: 1_000 });
  }).toPass({ timeout: 20_000 });

  const tz = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  const events = await (await page.request.get(`/api/events?range=today&tz=${encodeURIComponent(tz)}`)).json();
  const demo = events.find((e: { title: string }) => e.title === "Presales: demo prospect");
  await page.request.put(`/api/events/${demo.id}/client`, { data: { client_id: null }, headers: ORIGIN });  // start unlinked
  await page.reload();

  const select = card.getByLabel("Client");
  await expect(select).toHaveValue("");  // "Choose a client"
  const put = page.waitForResponse(r => r.url().endsWith(`/api/events/${demo.id}/client`) && r.request().method() === "PUT");
  await select.selectOption({ label: "Harbourline Legal" });
  expect((await put).status()).toBe(200);

  await page.reload();
  await expect(card.getByLabel("Client")).toHaveValue(String(client.id));
  await expect(card.getByLabel("Client").locator("option:checked")).toHaveText("Harbourline Legal");
  await page.screenshot({ path: "../docs/superpowers/screenshots/today.png", fullPage: true });
});
