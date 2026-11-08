// Gemini route: a 400 that rejects response_format must retry without it (prompt still carries the schema); fenced or prefixed JSON is parsed.
process.env.GEMINI_API_KEY = "test-gemini";
process.env.LOVABLE_API_KEY = "test-lovable";
delete process.env.OPENAI_API_KEY; delete process.env.ANTHROPIC_API_KEY;
const { PROVIDERS, isSchemaFormatError } = await import("./out/src/lib/ai/providers.server.js");
let f = 0; const t = (l, c, d = "") => { if (!c) { f++; console.log("FAIL", l, d); } else console.log("PASS", l); };
const gemini = PROVIDERS.find((p) => p.id === "gemini");
t("gemini provider present and direct", gemini && gemini.available() && gemini.model === "gemini-3-flash", gemini?.model);
const menu = { mode: "poster", restaurantName: "Yiros House", tagline: "Deals", eyebrow: "Sharing", sections: [{ title: "DEALS", note: null, items: [{ name: "Yiros Meal Deal", description: "3 lamb yiros and large chips", price: "$49.90", badge: "", photoPrompt: "Three lamb yiros wraps and a large basket of chips on a dark board, 45-degree angle, softbox light, true colour, square composition." }] }], footerLeft: "", footerRight: "", followed: ["Used exact prices"], features: [], heroPhoto: { label: "Feast", prompt: "A family table of yiros wraps and chips, overhead, soft light, wide banner." }, dishPhotos: [] };
const calls = [];
globalThis.fetch = async (url, init) => {
  const body = JSON.parse(init.body);
  calls.push({ url, format: body.response_format?.type, model: body.model });
  if (body.response_format?.type === "json_schema") return new Response(JSON.stringify({ error: { message: "Invalid argument: response_format.json_schema is not supported" } }), { status: 400 });
  return new Response(JSON.stringify({ choices: [{ message: { content: "Here is the menu:\n```json\n" + JSON.stringify(menu) + "\n```" } }] }), { status: 200, headers: { "Content-Type": "application/json" } });
};
const served = await gemini.generate({ brief: "Yiros Meal Deal $49.90", mode: "poster", style: "bold-deals", pages: "1", size: "poster" }, new AbortController().signal);
t("retried without json_schema on the same model", calls.length === 2 && calls[0].format === "json_schema" && calls[1].format === "json_object" && calls[0].model === calls[1].model, JSON.stringify(calls));
t("direct endpoint used", calls[0].url.includes("generativelanguage.googleapis.com"), calls[0].url);
t("fenced JSON with a prefix parsed", served.value.restaurantName === "Yiros House" && served.model === "gemini-3-flash");
t("isSchemaFormatError scoped to 400 + format words", isSchemaFormatError(400, "response_format not supported") && !isSchemaFormatError(400, "Not enough credits") && !isSchemaFormatError(429, "json_schema"));
console.log(f ? `FAILURES: ${f}` : "ALL PASS"); process.exit(f ? 1 : 0);
