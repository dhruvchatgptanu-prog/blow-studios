// Refreshes every "===== FILE: path =====" block in the apply bundles from the repo files,
// so the bundles always reproduce the GitHub state. Run from lovable/menu-master-ai:
//   node apply/regenerate.mjs
import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

const dir = new URL(".", import.meta.url).pathname;
const root = join(dir, "..");
const bundles = readdirSync(dir).filter((name) => /^round-.*-message-\d+\.md$/.test(name)).sort();
const seen = new Set();
const blockRe = /(===== FILE: (\S+) =====\n```(\w*)\n)([\s\S]*?)(\n```)/g;
for (const name of bundles) {
  const path = join(dir, name);
  const before = readFileSync(path, "utf8");
  const after = before.replace(blockRe, (whole, head, file, lang, _body, tail) => {
    seen.add(file);
    const source = join(root, file);
    if (!existsSync(source)) { console.warn(`missing in repo: ${file}`); return whole; }
    return `${head}${readFileSync(source, "utf8").replace(/\n$/, "")}${tail}`;
  });
  if (after !== before) writeFileSync(path, after);
  console.log(`${name}: ${after.length} chars${after.length > 100_000 ? "  OVER LIMIT" : ""}`);
}
const extra = (process.env.EXTRA_FILES ?? "").split(",").filter(Boolean);
for (const file of extra) if (!seen.has(file)) console.warn(`not in any bundle: ${file}`);
