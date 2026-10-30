// End-to-end test of composeMenu with a mocked AI gateway. No network, no keys, no credits.
// Run after compiling (see README): node ensemble.test.mjs
process.env.LOVABLE_API_KEY = "test-lovable";
process.env.OPENAI_API_KEY = "test-openai";
delete process.env.ANTHROPIC_API_KEY;
delete process.env.SUPABASE_URL; // memory off
process.env.HALFTONE_GENERATE_TIMEOUT_MS = "5000";
process.env.HALFTONE_JUDGE_TIMEOUT_MS = "5000";
process.env.HALFTONE_REPAIR_TIMEOUT_MS = "5000";

const { composeMenu } = await import("./out/src/lib/ai/ensemble.server.js");

const brief = "Create a bold family deals poster for Yiros House. Include: Yiros Meal Deal — 3 lamb yiros and large chips $49.90; Seafood Family Pack — 3 fish, calamari, prawns and chips $59.90; Big Family Yiros — 5 chicken yiros and family chips $69.90. Use the exact names and prices.";
const request = { brief, mode: "poster", style: "bold-deals", pages: "1", size: "poster" };

const good = {
  mode: "poster", restaurantName: "Yiros House", tagline: "Family Feast Deals", eyebrow: "Sharing deals",
  sections: [{ title: "SHARING DEALS", note: null, items: [
    { name: "Yiros Meal Deal", description: "3 lamb yiros with salad and tzatziki, plus a large serve of chips.", price: "$49.90", badge: "FEEDS 3", photoPrompt: "Three lamb yiros wraps cut diagonally and a large wire basket of chips." },
    { name: "Seafood Family Pack", description: "3 battered fish, calamari, prawns and chips with lemon and tartare.", price: "$59.90", badge: "", photoPrompt: "Seafood platter with three battered fish, calamari rings, prawns and chips." },
    { name: "Big Family Yiros", description: "5 chicken yiros with garlic sauce, plus a family serve of chips.", price: "$69.90", badge: "BIG VALUE", photoPrompt: "Five chicken yiros wraps fanned beside a family basket of chips." } ] }],
  footerLeft: "ORDER ONLINE", footerRight: "PICKUP", followed: ["Used exact names", "Used exact prices"], features: [],
  heroPhoto: { label: "Feast", prompt: "A family table of yiros wraps, chips and a seafood platter." }, dishPhotos: [],
};
const flawed = JSON.parse(JSON.stringify(good));
flawed.sections[0].items[1].price = "$59.00";
flawed.footerRight = "CALL 0412 345 678";

// ---- mock gateway ----
let scenario = {};
const calls = [];
function sse(text) {
  const frames = [`data: ${JSON.stringify({ type: "response.output_text.delta", delta: text })}\n\n`, "data: [DONE]\n\n"];
  return new Response(new ReadableStream({ start(c) { for (const f of frames) c.enqueue(new TextEncoder().encode(f)); c.close(); } }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}
function json(obj) { return new Response(JSON.stringify(obj), { status: 200, headers: { "Content-Type": "application/json" } }); }
function responsesJson(text) { return json({ output: [{ type: "message", content: [{ type: "output_text", text }] }] }); }
function err(status, message) { return new Response(JSON.stringify({ error: { message } }), { status }); }

globalThis.fetch = async (url, init) => {
  const body = JSON.parse(init.body);
  const provider = String(url).includes("lovable") ? "lovable" : "openai";
  const kind = body.instructions.startsWith("You are the Halftone editor-in-chief") ? "judge" : body.instructions.startsWith("You are the Halftone proofreader") ? "repair" : "generate";
  calls.push({ provider, kind, model: body.model, stream: body.stream });
  const handler = scenario[`${provider}.${kind}`];
  const result = typeof handler === "function" ? handler(body) : handler;
  if (result instanceof Response) return result;
  const text = JSON.stringify(result);
  return provider === "lovable" ? sse(text) : responsesJson(text);
};

let failures = 0;
function check(label, cond, detail = "") { if (!cond) { failures++; console.log("FAIL", label, detail); } else console.log("PASS", label); }

// Scenario 1: lovable flawed, openai good, judge (openai) returns good → judged-merge, no repair, score 100.
scenario = { "lovable.generate": flawed, "openai.generate": good, "openai.judge": { menu: good, winner: "openai", rationale: "Kept the exact prices from openai.", mergedFrom: ["openai"] } };
calls.length = 0;
let result = await composeMenu(request);
check("s1 strategy judged-merge", result.engine.strategy === "judged-merge", result.engine.strategy);
check("s1 final score 100", result.engine.finalScore === 100, String(result.engine.finalScore));
check("s1 no repair", !result.engine.repair);
check("s1 judge is openai", result.engine.judge?.provider === "openai");
check("s1 candidates scored", result.engine.candidates.find((c) => c.provider === "lovable")?.score < 100 && result.engine.candidates.find((c) => c.provider === "openai")?.score === 100);
check("s1 cuisine greek", result.engine.cuisine[0] === "greek / yiros / gyros / souvlaki", JSON.stringify(result.engine.cuisine));
check("s1 photo prompts enriched", result.sections[0].items[0].photoPrompt.includes("Square composition"), result.sections[0].items[0].photoPrompt.slice(-60));
check("s1 hero enriched wide", result.heroPhoto.prompt.includes("Wide banner composition"));
check("s1 judge call count", calls.filter((c) => c.kind === "judge").length === 1 && calls.filter((c) => c.kind === "generate").length === 2, JSON.stringify(calls));

// Scenario 2: both flawed, judge returns flawed, repair returns good → repair applied.
scenario = { "lovable.generate": flawed, "openai.generate": flawed, "openai.judge": { menu: flawed, winner: "lovable", rationale: "merged", mergedFrom: ["lovable"] }, "openai.repair": good };
calls.length = 0;
result = await composeMenu(request);
check("s2 repair ran and applied", result.engine.repair?.applied === true, JSON.stringify(result.engine.repair));
check("s2 final 100 after repair", result.engine.finalScore === 100, String(result.engine.finalScore));
check("s2 repair call made", calls.some((c) => c.kind === "repair"));

// Scenario 3: openai primary model retired → falls back; judge errors on non-model error → best candidate kept.
scenario = {
  "lovable.generate": flawed,
  "openai.generate": (body) => (body.model === "gpt-6-astra" ? err(404, "The model `gpt-6-astra` has been deprecated and shut down") : good),
  "openai.judge": err(429, "Rate limit reached"),
  "openai.repair": good,
};
calls.length = 0;
result = await composeMenu(request);
const openaiCandidate = result.engine.candidates.find((c) => c.provider === "openai");
check("s3 openai fell back to gpt-6.1-sol", openaiCandidate?.model === "gpt-6.1-sol" && openaiCandidate.status === "ok", JSON.stringify(openaiCandidate));
check("s3 two openai generate attempts", calls.filter((c) => c.provider === "openai" && c.kind === "generate").length === 2);
check("s3 judge failure recorded, best kept", result.engine.judge?.rationale.startsWith("Judge unavailable") && result.engine.strategy === "best-candidate", JSON.stringify(result.engine.judge));
check("s3 final 100 (best candidate was openai good)", result.engine.finalScore === 100, String(result.engine.finalScore));

// Scenario 4: every engine fails → clear error.
scenario = { "lovable.generate": err(500, "boom"), "openai.generate": err(400, "Invalid schema") };
let threw = "";
try { await composeMenu(request); } catch (e) { threw = e.message; }
check("s4 every engine failed error", threw.startsWith("Every engine failed."), threw);

// Scenario 5: malformed JSON from one engine is tolerated.
scenario = { "lovable.generate": sse("{not json"), "openai.generate": good, "openai.judge": { menu: good, winner: "openai", rationale: "ok", mergedFrom: ["openai"] } };
result = await composeMenu(request);
check("s5 malformed engine marked failed, run succeeds", result.engine.candidates.find((c) => c.provider === "lovable")?.status === "failed" && result.engine.finalScore === 100);

console.log(failures ? `FAILURES: ${failures}` : "ALL PASS");
process.exit(failures ? 1 : 0);
