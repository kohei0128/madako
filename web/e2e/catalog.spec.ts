import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  const explorer = page.locator(".explorer");
  await expect(explorer.locator(".model-item")).toHaveCount(5);
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
  const source = page.locator(".explorer").getByRole("button", { name: /source events$/ });
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

test("shows distinct count and ratio for string columns", async ({ page }) => {
  await page.getByRole("button", { name: "String" }).click();
  const category = page.locator("tbody tr").filter({ hasText: "category" });

  await expect(category.locator(".value-cell").nth(0)).toHaveText("2");
  await expect(category.locator(".value-cell").nth(1)).toHaveText("20.0%");
});

test("shows and navigates direct upstream and downstream lineage", async ({ page }) => {
  const lineage = page.getByLabel("Direct lineage");
  await expect(lineage.getByText("3 upstream · 1 downstream")).toBeVisible();
  const upstream = lineage.getByRole("button", { name: "Open source events" });
  const upstreamCards = lineage.locator(".lineage-column.upstream .lineage-card");
  const selected = lineage.locator(".lineage-card.current");
  const downstream = lineage.getByRole("button", { name: "Open model event_summary" });
  await expect(upstream).toBeVisible();
  await expect(downstream).toBeVisible();
  await expect(selected.locator(".lineage-kind")).toHaveText("model");
  await expect(selected.locator("small")).toHaveText("demo / analytics");
  const incomingArrow = lineage.locator(".lineage-arrow.incoming");
  const outgoingArrow = lineage.locator(".lineage-arrow.outgoing");
  const [firstUpstreamBox, selectedBox, downstreamBox, incomingArrowBox, outgoingArrowBox] = await Promise.all([
    upstreamCards.first().boundingBox(), selected.boundingBox(), downstream.boundingBox(),
    incomingArrow.boundingBox(), outgoingArrow.boundingBox(),
  ]);
  const selectedCenter = selectedBox!.y + selectedBox!.height / 2;
  expect(Math.abs(firstUpstreamBox!.y - selectedBox!.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(downstreamBox!.y - selectedBox!.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(incomingArrowBox!.y + incomingArrowBox!.height / 2 - selectedCenter)).toBeLessThanOrEqual(1);
  expect(Math.abs(outgoingArrowBox!.y + outgoingArrowBox!.height / 2 - selectedCenter)).toBeLessThanOrEqual(1);

  await upstream.click();

  await expect(page.locator(".title-row h1")).toHaveText("events");
  await expect(page.locator(".pill")).toHaveText("source");
  await expect(page.getByLabel("Direct lineage").locator(".lineage-card.current .lineage-kind")).toHaveText("source");
  await expect(page.getByLabel("Direct lineage").getByRole("button", { name: "Open model events" })).toBeVisible();
});

test("omits empty lineage directions and their arrows", async ({ page }) => {
  const lineage = page.getByLabel("Direct lineage");

  await lineage.getByRole("button", { name: "Open model event_summary" }).click();

  await expect(page.locator(".title-row h1")).toHaveText("event_summary");
  await expect(lineage.locator(".lineage-column.downstream")).toBeEmpty();
  await expect(lineage.locator(".lineage-arrow.outgoing")).toHaveCount(0);
  await expect(lineage.getByText("No downstream relations")).toHaveCount(0);

  await page.locator(".explorer").getByRole("button", { name: /source events$/ }).click();

  await expect(page.locator(".title-row h1")).toHaveText("events");
  await expect(lineage.locator(".lineage-column.upstream")).toBeEmpty();
  await expect(lineage.locator(".lineage-arrow.incoming")).toHaveCount(0);
  await expect(lineage.getByText("No upstream relations")).toHaveCount(0);
});
