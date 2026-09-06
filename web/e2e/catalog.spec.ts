import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".model-item")).toHaveCount(2);
});

test("selects same-name relations by unique id", async ({ page }) => {
  const source = page.locator(".model-item").filter({ hasText: "source" });
  await source.click();
  await expect(page.locator(".pill")).toHaveText("source");
  await expect(page.getByText("Metadata available")).toBeVisible();

  const model = page.locator(".model-item").filter({ hasText: "model" });
  await model.click();
  await expect(page.getByText("10 total rows")).toBeVisible();
});

test("refreshes metadata and keeps the selected relation", async ({ page }) => {
  const source = page.locator(".model-item").filter({ hasText: "source" });
  await source.click();
  const refreshed = page.waitForResponse(
    (response) => response.url().includes("/api/models?include_profiles=false"),
  );

  await page.getByRole("button", { name: "Refresh" }).click();

  expect((await refreshed).status()).toBe(200);
  await expect(source).toHaveClass(/selected/);
});

test("shows the null date partition separately", async ({ page }) => {
  await page.getByRole("button", { name: "event_date" }).click();
  await expect(page.getByText("NULL partition · 2 rows")).toBeVisible();
  await expect(page.getByText("Latest partition").locator("..")).toContainText("2026-09-01");
});
