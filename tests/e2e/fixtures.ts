import { test, expect, type Page } from "@playwright/test";

export const CASE_ID =
  process.env.E2E_CASE_ID || "38865959-8d5f-44e4-bbc8-1226bd345b00";

export { test, expect };

export async function gotoCase(page: Page, suffix = "") {
  await page.goto(`/dashboard/procesos/${CASE_ID}${suffix}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(
    page.getByRole("navigation", { name: "Secciones del proceso" }),
  ).toBeVisible();
}
