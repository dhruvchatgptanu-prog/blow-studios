import { contrastRatio, parseColor, themeContrast } from "./out/src/lib/ai/color.js";
import { TEMPLATES, recommendTemplates } from "./out/src/lib/ai/templates.js";
import { profileFromText, mergeProfiles } from "./out/src/lib/ai/store.js";
import { resolveDisplay } from "./out/src/lib/ai/display.js";
let f = 0; const t = (l, c, d = "") => { if (!c) { f++; console.log("FAIL", l, d); } else console.log("PASS", l); };
t("black on white = 21", contrastRatio("#000000", "#ffffff") === 21);
t("oklch white ≈ 1 luminance", Math.abs(parseColor("oklch(1 0 0)").r - 1) < 0.01);
t("oklch black ≈ 0", parseColor("oklch(0 0 0)").r < 0.01);
t("percent lightness accepted", contrastRatio("oklch(23% 0.03 55)", "oklch(94.5% 0.028 85)") > 10);
const base = themeContrast({});
t("default tokens pass AA", base.level !== "fail" && base.inkOnPaper >= 7, JSON.stringify(base));
const failing = [];
for (const tpl of TEMPLATES) { const c = themeContrast(tpl.theme); console.log(`  ${tpl.id.padEnd(18)} weakest ${String(c.weakest).padStart(5)}  ink/paper ${c.inkOnPaper} poster ${c.posterText} price ${c.priceOnCard} accent ${c.accentOnCard} muted ${c.mutedOnCard} → ${c.level}`); if (c.level === "fail") failing.push(tpl.id); }
t("no template fails WCAG AA on reading text", failing.length === 0, failing.join(","));
const dim = recommendTemplates(mergeProfiles(profileFromText("Pub with dim lighting, wall board behind the bar", "The Crown"), { cuisine: "pub" }));
t("dim pub gets a contrast reason", dim[0].reasons.some((r) => r.includes("contrast")) || dim[0].template.id === "chalkboard-pub", JSON.stringify(dim[0]));
const guess = resolveDisplay({ displayType: "wall-board", corners: [], estimatedWidthCm: 150, estimatedHeightCm: 90, references: [], viewingDistanceM: 3, mountedHeightCm: 0, lighting: "neutral", isDigital: false, existingContentSummary: "", confidence: 0.9, notes: "" });
t("no reference object caps confidence", guess.sizeConfidence <= 0.3 && guess.notes.some((n) => n.includes("no reference object")), JSON.stringify([guess.sizeConfidence, guess.notes]));
console.log(f ? `FAILURES: ${f}` : "ALL PASS"); process.exit(f ? 1 : 0);
