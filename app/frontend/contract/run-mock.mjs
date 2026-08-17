#!/usr/bin/env node
/**
 * Golden contract runner — bundles the TS driver with esbuild (already a
 * frontend dependency, so no new tooling) and prints the mock's canonical
 * transcript as JSON on stdout.
 *
 * Usage: node app/frontend/contract/run-mock.mjs [cases-dir]   (from anywhere)
 *
 * `tests/test_contract.py` shells out to this and diffs the transcript against
 * the FastAPI TestClient's. Run it by hand to see the mock's half on its own.
 */

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";

const here = path.dirname(fileURLToPath(import.meta.url)); // frontend/contract
const root = path.resolve(here, "../..");
const casesDir = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(root, "contract", "cases");

const built = buildSync({
  entryPoints: [path.join(here, "mock-driver.ts")],
  bundle: true,
  platform: "node",
  format: "esm",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const driver = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

const cases = readdirSync(casesDir)
  .filter((f) => f.endsWith(".json"))
  .sort()
  .map((f) => JSON.parse(readFileSync(path.join(casesDir, f), "utf8")));

process.stdout.write(JSON.stringify(driver.runCases(cases)));
