import { isModelError, modelChain } from "./out/src/lib/ai/providers.server.js";
const cases = [
  ["The model `gpt-6-astra` does not exist or you do not have access to it.", true],
  ["model_not_found: The requested model has been deprecated", true],
  ["Model gpt-6-astra was shut down on 2026-10-23", true],
  ["Invalid schema for response_format: additionalProperties", false],
  ["Rate limit reached for requests", false],
  ["Timed out.", false],
  ["The engine returned malformed JSON.", false],
  ["not_found_error: model: claude-opus-5-5", true],
];
let fails = 0;
for (const [msg, expected] of cases) { const got = isModelError(msg); if (got !== expected) { fails++; console.log("FAIL", msg, "->", got); } }
console.log("chains", JSON.stringify({ openai: modelChain("openai"), lovable: modelChain("lovable"), claude: modelChain("claude") }));
console.log(fails ? `FAILURES: ${fails}` : "ALL PASS");
process.exit(fails ? 1 : 0);
