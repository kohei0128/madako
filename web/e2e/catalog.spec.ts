import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  const explorer = page.locator(".explorer");
  await expect(explorer.locator(".model-item")).toHaveCount(3);
  await explorer.getByRole("button", { name: /model events$/ }).click();
});

test("selects same-name relations by unique id", async ({ page }) => {
  const explorer = page.locator(".explorer");
  const source = explorer.getByRole("button", { name: /source events$/ });
  await source.click();
  await expect(page.locator(".pill")).toHaveText("source");
  await expect(page.getByText("Metadata available")).toBeVisible();

  const model = explorer.getByRole("button", { name: /model events$/ });
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

test("shows and navigates direct upstream and downstream lineage", async ({ page }) => {
  const lineage = page.getByLabel("Direct lineage");
  await expect(lineage.getByText("1 upstream · 1 downstream")).toBeVisible();
  await expect(lineage.getByRole("button", { name: "Open source events" })).toBeVisible();
  await expect(lineage.getByRole("button", { name: "Open model event_summary" })).toBeVisible();

  await lineage.getByRole("button", { name: "Open source events" }).click();

  await expect(page.locator(".title-row h1")).toHaveText("events");
  await expect(page.locator(".pill")).toHaveText("source");
  await expect(page.getByLabel("Direct lineage").getByRole("button", { name: "Open model events" })).toBeVisible();
});
