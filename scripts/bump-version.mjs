#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

const newVersion = process.argv[2];
if (!newVersion || !/^\d+\.\d+\.\d+$/.test(newVersion)) {
  console.error("Usage: npm run bump <x.y.z>");
  console.error("Example: npm run bump 0.1.4");
  process.exit(1);
}

const pkgPath = resolve(root, "package.json");
const tauriConfPath = resolve(root, "src-tauri", "tauri.conf.json");
const cargoTomlPath = resolve(root, "src-tauri", "Cargo.toml");

const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
pkg.version = newVersion;
writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n");

const tauriConf = JSON.parse(readFileSync(tauriConfPath, "utf8"));
tauriConf.version = newVersion;
writeFileSync(tauriConfPath, JSON.stringify(tauriConf, null, 2) + "\n");

const cargoToml = readFileSync(cargoTomlPath, "utf8");
const updated = cargoToml.replace(
  /^version\s*=\s*"[^"]+"/m,
  `version = "${newVersion}"`,
);
if (updated === cargoToml) {
  console.error("Could not find version line in Cargo.toml");
  process.exit(1);
}
writeFileSync(cargoTomlPath, updated);

console.log(`Bumped to ${newVersion} in:`);
console.log(`  - package.json`);
console.log(`  - src-tauri/tauri.conf.json`);
console.log(`  - src-tauri/Cargo.toml`);
console.log("");
console.log("Next steps:");
console.log(`  git add -A && git commit -m "chore: bump version to ${newVersion}"`);
console.log(`  git tag v${newVersion}`);
console.log(`  git push && git push --tags`);
