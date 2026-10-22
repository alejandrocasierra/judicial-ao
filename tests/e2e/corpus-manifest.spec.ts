import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

/**
 * Prueba simple de reproducibilidad del manifiesto legal.
 * No abre navegador: valida que checksums.json coincida con el corpus.
 */
test("el manifiesto legal está actualizado y es reproducible", () => {
  const root = path.resolve(__dirname, "..", "..");
  const candidates = [
    path.join(root, ".venv", "Scripts", "python.exe"),
    path.join(root, ".venv", "bin", "python"),
    "python",
    "python3",
  ];
  const python = candidates.find((candidate) =>
    candidate.includes(path.sep) ? fs.existsSync(candidate) : true,
  );
  expect(python, "no se encontró un intérprete de Python").toBeTruthy();

  const output = execFileSync(
    python as string,
    ["scripts/build_checksums.py", "--check"],
    { cwd: root, encoding: "utf8" },
  );
  const manifest = JSON.parse(
    fs.readFileSync(path.join(root, "data", "legal_rules", "co", "checksums.json"), "utf8"),
  );
  expect(output).toContain("coincide");
  expect(output).toContain(`${manifest.rules_count} normas`);
});
