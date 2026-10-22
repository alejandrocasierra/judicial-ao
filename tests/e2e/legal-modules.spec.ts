import { test, expect, gotoCase } from "./fixtures";

test.describe("módulos legales: pestañas y navegación", () => {
  test("Expediente/Hechos/Auditoría/Verificación están presentes y navegan", async ({ page }) => {
    await gotoCase(page);
    const tabs = page.getByRole("navigation", { name: "Secciones del proceso" });
    await expect(tabs.getByRole("link", { name: "Expediente" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await tabs.getByRole("link", { name: "Hechos" }).click();
    await expect(page).toHaveURL(/\/hechos$/);
    await expect(
      page.getByRole("heading", { name: "Hechos del proceso" }),
    ).toBeVisible();
    await expect(tabs.getByRole("link", { name: "Hechos" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await tabs.getByRole("link", { name: "Auditoría" }).click();
    await expect(page).toHaveURL(/\/auditoria$/);
    await expect(
      page.getByRole("heading", { name: "Auditoría legal" }),
    ).toBeVisible();
    await expect(tabs.getByRole("link", { name: "Auditoría" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await tabs.getByRole("link", { name: "Verificación" }).click();
    await expect(page).toHaveURL(/\/verificacion$/);
    await expect(
      page.getByRole("heading", { name: "Verificación del proceso" }),
    ).toBeVisible();
    await expect(page.getByLabel("ID del proceso")).toHaveCount(0);
    await expect(tabs.getByRole("link", { name: "Verificación" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await tabs.getByRole("link", { name: "Expediente" }).click();
    await expect(page).toHaveURL(new RegExp(`/dashboard/procesos/[^/]+$`));
  });
});

test.describe("tablero de hechos", () => {
  test("filtros, búsqueda y detalle de un hecho", async ({ page }) => {
    await gotoCase(page, "/hechos");
    await expect(
      page.getByRole("heading", { name: "Hechos del proceso" }),
    ).toBeVisible();

    const tablist = page.getByRole("tablist", { name: "Estado de prueba" });
    for (const name of ["Probados", "Con prueba", "Discutidos", "Todos"]) {
      await expect(tablist.getByRole("tab", { name })).toBeVisible();
    }

    const allResponse = page.waitForResponse(
      (r) => r.url().includes("/facts?") && r.url().includes("proof=all"),
    );
    await tablist.getByRole("tab", { name: "Todos" }).click();
    await allResponse;
    await expect(tablist.getByRole("tab", { name: "Todos" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    const sideResponse = page.waitForResponse(
      (r) => r.url().includes("/facts?") && r.url().includes("side=DEMANDANTE"),
    );
    await page.getByRole("button", { name: "Demandante", exact: true }).click();
    await sideResponse;

    const searchResponse = page.waitForResponse(
      (r) => r.url().includes("/facts?") && r.url().includes("q=pago"),
    );
    await page.getByRole("textbox", { name: "Buscar hecho" }).fill("pago");
    await searchResponse;
    await expect(page.getByRole("textbox", { name: "Buscar hecho" })).toHaveValue(
      "pago",
    );

    // Con "Todos" y sin búsqueda hay hechos locales; abrimos el primero.
    await page.getByRole("textbox", { name: "Buscar hecho" }).fill("");
    await page.waitForTimeout(500);
    const firstFact = page.locator("div.space-y-3 > button").first();
    await expect(firstFact).toBeVisible();
    await firstFact.click();
    await expect(page.getByText("Revisar estado de prueba")).toBeVisible();
    await expect(page.getByText("Posiciones")).toBeVisible();
    await page.keyboard.press("Escape");
  });
});

test.describe("panel de auditoría y segunda opinión", () => {
  test("muestra auditoría, claims, hallazgos y botón de segunda opinión", async ({
    page,
  }) => {
    await gotoCase(page, "/auditoria");
    await expect(
      page.getByRole("heading", { name: "Auditoría legal" }),
    ).toBeVisible();

    // La base local tiene una auditoría real (PASSED) para el caso piloto.
    const auditCard = page.locator("div.space-y-2 > button").first();
    await expect(auditCard).toBeVisible();
    await expect(auditCard.getByText("Verificado")).toBeVisible();
    await auditCard.click();

    await expect(page.getByRole("heading", { name: "Claims" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Hallazgos" })).toBeVisible();
    const secondOpinion = page.getByRole("button", {
      name: "Solicitar segunda opinión",
    });
    await expect(secondOpinion).toBeVisible();
    await expect(secondOpinion).toBeEnabled();
  });

  test("el botón de segunda opinión dispara el POST sin costo real", async ({
    page,
  }) => {
    await gotoCase(page, "/auditoria");
    const auditCard = page.locator("div.space-y-2 > button").first();
    await expect(auditCard).toBeVisible();
    await auditCard.click();
    await expect(page.getByRole("heading", { name: "Claims" })).toBeVisible();

    let requested = false;
    await page.route("**/second-opinion", async (route) => {
      requested = true;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ run_id: "e2e-run", status: "QUEUED" }),
      });
    });

    const button = page.getByRole("button", { name: "Solicitar segunda opinión" });
    await expect(button).toBeVisible();
    await button.click();
    await expect.poll(() => requested, { timeout: 10_000 }).toBe(true);
    await expect(
      page.getByText("No se pudo solicitar. Revisa el modelo de segunda opinión"),
    ).toHaveCount(0);
  });
});

test.describe("responsive 360/768/1024/1440", () => {
  test("las cuatro pestañas caben y no hay desbordamiento horizontal", async ({
    page,
  }) => {
    await gotoCase(page);
    const tabs = page.getByRole("navigation", { name: "Secciones del proceso" });
    await expect(tabs.getByRole("link", { name: "Expediente" })).toBeVisible();
    await expect(tabs.getByRole("link", { name: "Hechos" })).toBeVisible();
    await expect(tabs.getByRole("link", { name: "Auditoría" })).toBeVisible();
    await expect(tabs.getByRole("link", { name: "Verificación" })).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(2);

    await tabs.getByRole("link", { name: "Hechos" }).click();
    await expect(
      page.getByRole("heading", { name: "Hechos del proceso" }),
    ).toBeVisible();
    const factsOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(factsOverflow).toBeLessThanOrEqual(2);

    await tabs.getByRole("link", { name: "Auditoría" }).click();
    await expect(
      page.getByRole("heading", { name: "Auditoría legal" }),
    ).toBeVisible();
    const auditOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(auditOverflow).toBeLessThanOrEqual(2);
  });
});
