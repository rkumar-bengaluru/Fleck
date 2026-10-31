import { expect, test } from "@playwright/test";

// Not run in Task 21 (the machine stays cool). Needs a ready, indexed meeting; run after the replay spec.
test("library lists documents and the cross-meeting chat answers with a source link", async ({ page }) => {
  await page.goto("/signin?next=%2Flibrary");
  await page.getByRole("button", { name: "Sign in with Google" }).click();
  await page.getByRole("button", { name: "rupak@northbeam.test" }).click();
  await page.getByRole("button", { name: "Allow" }).click();
  await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Used in answers" })).toBeVisible();
  await page.getByRole("link", { name: "Ask across meetings" }).click();
  await page.getByRole("textbox", { name: "Ask across meetings" }).fill("What did the client say about where documents can live?");
  await page.getByRole("button", { name: "Ask" }).click();
  await page.getByRole("list", { name: "Sources" }).last().getByRole("link").first().click();
  await expect(page).toHaveURL(/\/meetings\/\d+\?t=/);
});
