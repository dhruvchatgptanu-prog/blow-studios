import { isModelError, modelChain, isCreditError, engineStatus } from "./out/src/lib/ai/providers.server.js";
const cases = [
  ["The model `gpt-6-astra` does not exist or you do not have access to it.", true],
  ["model_not_found: The requested model has been deprecated", true],
  ["Model gpt-6-astra was shut down on 2026-10-23", true],
  ["Invalid schema for response_format: additionalProperties", false],
  ["Rate limit reached for requests", true], // per-model rate limit → try the next model
  ["Timed out.", false],
  ["The engine returned malformed JSON.", false],
  ["not_found_error: model: claude-opus-5-5", true],
];
let fails = 0;
cases.push(["429 RESOURCE_EXHAUSTED: quota exceeded for gemini-3-pro per day", true]);
cases.push(["You exceeded your current quota, please check your plan and billing details", false]);
cases.push(["Not enough credits", false]);
for (const [msg, expected] of cases) { const got = isModelError(msg); if (got !== expected) { fails++; console.log("FAIL", msg, "->", got); } }
console.log("chains", JSON.stringify({ openai: modelChain("openai"), lovable: modelChain("lovable"), claude: modelChain("claude") }));
for (const [msg, expected] of [["Not enough credits", true], ["insufficient_quota", true], ["The engine returned malformed JSON.", false], ["Timed out.", false]]) { if (isCreditError(msg) !== expected) { fails++; console.log("FAIL credit", msg); } }
process.env.OPENAI_API_KEY = "test"; process.env.LOVABLE_API_KEY = "test"; delete process.env.ANTHROPIC_API_KEY; delete process.env.GEMINI_API_KEY;
const status = engineStatus();
if (!(status.length === 4 && status.find((e) => e.id === "openai")?.configured === true && status.find((e) => e.id === "claude")?.configured === false && status.find((e) => e.id === "gemini")?.route === "gateway")) { fails++; console.log("FAIL engineStatus", JSON.stringify(status)); }
console.log(fails ? `FAILURES: ${fails}` : "ALL PASS");
process.exit(fails ? 1 : 0);
