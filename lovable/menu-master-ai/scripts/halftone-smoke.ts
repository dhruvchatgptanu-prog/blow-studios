/**
 * End-to-end smoke test run inside the Lovable sandbox (real engines, real storage):
 *   bun run scripts/halftone-smoke.ts
 * Prints which engines answered, the verifier score, the fit, memory and storage status.
 * Exit code 1 when no engine produced a design or storage rejected an upload.
 */
import { availableProviders } from "@/lib/ai/providers.server";
import { composeMenu } from "@/lib/ai/ensemble.server";
import { displayFromWords } from "@/lib/ai/display";
import { signAssetPaths, storeAssets } from "@/lib/ai/assets.server";
import { parseIntent } from "@/lib/ai/intent";

process.env["HALFTONE_GENERATE_TIMEOUT_MS"] ??= "90000";
process.env["HALFTONE_JUDGE_TIMEOUT_MS"] ??= "90000";
process.env["HALFTONE_REPAIR_TIMEOUT_MS"] ??= "60000";

const brief = "Create a bold family deals board 120 x 60 cm above the counter for Yiros House. Include: Yiros Meal Deal — 3 lamb yiros and large chips $49.90; Seafood Family Pack — 3 fish, calamari, prawns and chips $59.90; Big Family Yiros — 5 chicken yiros and family chips $69.90. Use the exact names and prices.";
const engines = availableProviders().map((provider) => `${provider.id} (${provider.model})`);
console.log("ENGINES:", engines.join(", ") || "none");
const intent = parseIntent(brief, { hasMenu: false, mode: "poster", size: "poster", pages: "1", style: "bold-deals" });
const display = displayFromWords(brief);
console.log("INTENT:", intent.kind, intent.mode, intent.size, "look:", intent.look?.id, "display:", display ? `${display.widthCm}x${display.heightCm} ${display.displayType} maxOffers=${display.budget.maxOffers}` : "none");

let failed = false;
const started = Date.now();
try {
  const result = await composeMenu({ brief, mode: intent.mode, style: intent.style, pages: intent.pages, size: intent.size, ...(display ? { display } : {}), ...(intent.look ? { template: intent.look.id } : {}) });
  const engine = result.engine;
  for (const candidate of engine.candidates) console.log(`CANDIDATE ${candidate.provider} ${candidate.model}: ${candidate.status} score=${candidate.score} ${candidate.ms}ms${candidate.error ? ` error=${candidate.error.slice(0, 160)}` : ""}`);
  console.log("STRATEGY:", engine.strategy, "judge:", engine.judge ? `${engine.judge.provider}/${engine.judge.model} base=${engine.judge.winner}` : "none", "repair:", engine.repair ? `${engine.repair.provider} ${engine.repair.before}->${engine.repair.after}` : "none");
  console.log("FINAL:", engine.finalScore, engine.finalIssues.length ? engine.finalIssues.join(" | ") : "no issues", "fit:", engine.fit ? `${engine.fit.format} omitted=${engine.fit.omitted.length}` : "none");
  console.log("MEMORY:", engine.memory.status ?? "unknown", engine.memory.detail ?? "");
  console.log("MENU:", result.restaurantName, result.sections.flatMap((section) => section.items).map((item) => `${item.name} ${item.price}`).join(" | "));
  console.log("TOTAL:", Date.now() - started, "ms");
  if (engine.candidates.every((candidate) => candidate.status !== "ok")) failed = true;
} catch (error) {
  console.log("COMPOSE FAILED:", error instanceof Error ? error.message : String(error));
  failed = true;
}

const pixel = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";
const stored = await storeAssets([{ kind: "menu", dataUrl: pixel, label: "smoke.png", conversationId: "smoke" }]);
console.log("STORAGE:", stored.assets.length ? `stored ${stored.assets[0]?.path} signed=${Boolean(stored.assets[0]?.url)}` : "nothing stored", stored.warnings.join(" | "));
if (stored.assets[0]) {
  const signed = await signAssetPaths([stored.assets[0].path]);
  console.log("RESIGN:", Object.keys(signed).length ? "ok" : "no url");
} else failed = true;
console.log(failed ? "SMOKE: FAILED" : "SMOKE: PASSED");
process.exit(failed ? 1 : 0);
