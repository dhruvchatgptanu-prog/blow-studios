import Anthropic from "@anthropic-ai/sdk";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";
import type { z } from "zod/v4";

import { cuisineCues } from "./cuisine";
import { displayBriefLines } from "./display";
import { describeFormat, JUDGE_SYSTEM_PROMPT, REPAIR_SYSTEM_PROMPT, SYSTEM_PROMPT } from "./knowledge";
import { describeTemplate, templateById } from "./templates";
import {
  generatedMenuSchema,
  JUDGE_JSON_SCHEMA,
  judgeWireSchema,
  MENU_JSON_SCHEMA,
  menuWireSchema,
  type GeneratedMenuCore,
  type MenuRequest,
  type ProviderId,
} from "./schema";

/**
 * Three engines, one contract.
 *
 *  lovable  → Lovable AI gateway (the app's own built-in AI; no extra key)
 *  openai   → ChatGPT models via the OpenAI Responses API   (OPENAI_API_KEY)
 *  claude   → Claude via the official Anthropic SDK          (ANTHROPIC_API_KEY)
 *
 * Each provider exposes `generate` (candidate menu) and `judge` (merge/select
 * the best final menu). Any provider can be missing; the ensemble copes.
 */

export type JudgeInput = {
  request: MenuRequest;
  candidates: { provider: ProviderId; model: string; menu: GeneratedMenuCore; score: number; issues: string[] }[];
};

export type JudgeOutput = z.infer<typeof judgeWireSchema>;

export type RepairInput = { request: MenuRequest; menu: GeneratedMenuCore; issues: string[] };

/** Every call reports the model that actually answered, because a fallback model may have stepped in. */
export type Served<T> = { value: T; model: string };

export type Provider = {
  id: ProviderId;
  label: string;
  model: string;
  available: () => boolean;
  generate: (request: MenuRequest, signal: AbortSignal) => Promise<Served<GeneratedMenuCore>>;
  judge: (input: JudgeInput, signal: AbortSignal) => Promise<Served<JudgeOutput>>;
  repair: (input: RepairInput, signal: AbortSignal) => Promise<Served<GeneratedMenuCore>>;
};

const LOVABLE_GATEWAY = "https://ai.gateway.lovable.dev/v1/responses";
const LOVABLE_GATEWAY_CHAT = "https://ai.gateway.lovable.dev/v1/chat/completions";
const OPENAI_RESPONSES = "https://api.openai.com/v1/responses";
/** Google's OpenAI-compatible endpoint (GEMINI_API_KEY). Without a key, Gemini runs through the Lovable gateway's google/* models. */
const GEMINI_CHAT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions";

const MODELS = {
  lovable: process.env["LOVABLE_TEXT_MODEL"] ?? "openai/gpt-6-astra",
  openai: process.env["OPENAI_MODEL"] ?? "gpt-6-astra",
  claude: process.env["ANTHROPIC_MODEL"] ?? "claude-opus-5-5",
  // Gateway ids come from Lovable's changelog (google/gemini-3.7-flash is confirmed); unknown ids fall through the chain.
  gemini: process.env["GEMINI_API_KEY"] ? (process.env["GEMINI_MODEL"] ?? "gemini-3-flash") : (process.env["LOVABLE_GEMINI_MODEL"] ?? "google/gemini-3.7-flash"),
} as const;

function list(env: string | undefined, fallback: string[]) {
  const values = (env ?? "").split(",").map((value) => value.trim()).filter(Boolean);
  return values.length ? values : fallback;
}

/**
 * Model fallback chains. When the primary model is retired or renamed (OpenAI
 * publishes shutdown dates per model id), the next id is tried automatically.
 * Override with comma-separated env vars; the primary MODELS entry always goes first.
 */
const FALLBACK_MODELS: Record<ProviderId, string[]> = {
  lovable: list(process.env["LOVABLE_FALLBACK_MODELS"], ["openai/gpt-6.1-sol", "openai/gpt-6-sol", "openai/gpt-6-luna", "openai/gpt-5.6-sol"]),
  openai: list(process.env["OPENAI_FALLBACK_MODELS"], ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5"]),
  claude: list(process.env["ANTHROPIC_FALLBACK_MODELS"], ["claude-sonnet-5-5", "claude-opus-5"]),
  gemini: process.env["GEMINI_API_KEY"]
    ? list(process.env["GEMINI_FALLBACK_MODELS"], ["gemini-2.5-flash", "gemini-3-flash-lite", "gemini-2.5-flash-lite", "gemini-3-pro"])
    : list(process.env["LOVABLE_GEMINI_FALLBACK_MODELS"], ["google/gemini-3.8-flash", "google/gemini-4-argon", "google/gemini-3.6-flash", "google/gemini-3.5-flash", "google/gemini-2.5-flash"]),
};

export function modelChain(id: ProviderId) {
  return Array.from(new Set([MODELS[id], ...FALLBACK_MODELS[id]]));
}

/** True when an error is about the model id itself (unknown, retired, not permitted), not about the request. */
export function isModelError(message: string) {
  const lower = message.toLowerCase();
  if (/abort|timed out|timeout/.test(lower)) return false;
  // A per-model quota (free tiers meter each model separately) is worth trying the next model for; a spent account is not.
  if (/resource_exhausted|rate limit|too many requests|\b429\b|quota exceeded|per[- ]day|per[- ]minute/.test(lower) && !/insufficient_quota|billing|not enough credits|payment/.test(lower)) return true;
  return /model/.test(lower) && /not[ _](found|exist|available|supported)|does not exist|unknown|invalid|deprecat|retired|decommission|shut ?down|no longer|permission|access|\b404\b/.test(lower);
}

/** An engine failed because the account behind it has no credit, not because of the brief. */
export function isCreditError(message: string) {
  return /not enough credits|insufficient_quota|insufficient credits|exceeded your current quota|billing|payment required|\b402\b|credit balance/i.test(message);
}

export type EngineStatus = { id: ProviderId; label: string; model: string; route: "direct" | "gateway"; configured: boolean; hint: string };

/** Which engines are configured right now, without calling any of them (shown in the chat so the owner can see keys being picked up). */
export function engineStatus(): EngineStatus[] {
  const lovableKey = Boolean(process.env["LOVABLE_API_KEY"]);
  const gemini = Boolean(process.env["GEMINI_API_KEY"]);
  return [
    { id: "lovable", label: "Halftone built-in", model: MODELS.lovable, route: "gateway", configured: lovableKey, hint: lovableKey ? "Uses Lovable AI credits." : "LOVABLE_API_KEY missing." },
    { id: "openai", label: "ChatGPT", model: MODELS.openai, route: "direct", configured: Boolean(process.env["OPENAI_API_KEY"]), hint: process.env["OPENAI_API_KEY"] ? "Your OpenAI account." : "Add OPENAI_API_KEY (platform.openai.com) in Lovable Cloud secrets." },
    { id: "claude", label: "Claude", model: MODELS.claude, route: "direct", configured: Boolean(process.env["ANTHROPIC_API_KEY"]), hint: process.env["ANTHROPIC_API_KEY"] ? "Your Anthropic account." : "Add ANTHROPIC_API_KEY (console.anthropic.com) in Lovable Cloud secrets." },
    { id: "gemini", label: "Google Gemini", model: MODELS.gemini, route: gemini ? "direct" : "gateway", configured: gemini || (lovableKey && process.env["HALFTONE_GATEWAY_GEMINI"] !== "off"), hint: gemini ? "Your Google account (free tier works)." : lovableKey ? "Via the Lovable gateway (uses Lovable AI credits); add GEMINI_API_KEY for a free direct key." : "Add GEMINI_API_KEY (aistudio.google.com)." },
  ];
}

/** Runs `attempt` for each model in the chain until one succeeds or a non-model error occurs. */
async function withModelFallback<T>(models: string[], attempt: (model: string) => Promise<T>): Promise<Served<T>> {
  let lastError: unknown;
  for (const [index, model] of models.entries()) {
    try {
      return { value: await attempt(model), model };
    } catch (error) {
      lastError = error;
      const message = error instanceof Error ? error.message : String(error);
      if (index === models.length - 1 || !isModelError(message)) throw error;
    }
  }
  throw lastError instanceof Error ? lastError : new Error("No model available.");
}

// ---------- shared prompt assembly ----------

/** Every reference image for a request, single or multiple, as data URLs. */
export function referenceImagesOf(request: MenuRequest): string[] {
  return [...(request.referenceImage ? [request.referenceImage] : []), ...(request.referenceImages ?? [])].slice(0, 8);
}

export function buildUserMessage(request: MenuRequest) {
  return [
    `BRIEF:\n${request.brief}`,
    `MODE: ${request.mode.toUpperCase()}`,
    `TYPE STYLE: ${request.style}`,
    `PAGE COUNT: ${request.pages}`,
    request.display ? displayBriefLines(request.display).join("\n") : `FORMAT: ${request.size} — ${describeFormat(request.size)}`,
    request.store ? `VENUE PROFILE: ${request.store.venueName || "unnamed venue"} — ${request.store.storeType.replace("-", " ")}, ${request.store.priceTier} price point${request.store.cuisine ? `, ${request.store.cuisine}` : ""}${request.store.audience ? `, audience: ${request.store.audience}` : ""}. Copy voice: ${request.store.voice}${request.store.signatureItems?.length ? ` Signature items (keep them, give them the hero spots): ${request.store.signatureItems.join(", ")}.` : ""}` : "",
    request.template && templateById(request.template) ? describeTemplate(templateById(request.template)!) : "",
    request.cuisine?.length ? `DETECTED CUISINE: ${request.cuisine.join(" + ")}. Apply these plating cues to every photo brief:\n${cuisineCues(request.cuisine).map((line) => `- ${line}`).join("\n")}` : "",
    referenceImagesOf(request).length > 1
      ? `${referenceImagesOf(request).length} photos of the existing menu are attached (pages, boards or both sides of one menu). Transcribe EVERY item, price and section from ALL of them into one design, in the order the photos were given; never drop a page; the written brief wins on any conflict.`
      : referenceImagesOf(request).length === 1 ? "A photo or screenshot of the existing menu is attached. Transcribe it faithfully, then redesign; the written brief wins on any conflict." : "",
    request.previous ? `PREVIOUS MENU (revise it, keep everything the new brief does not change):\n${request.previous}` : "",
    request.exemplars ? `APPROVED PAST OUTPUTS for a similar brief (match their precision and tone, never copy their content):\n${request.exemplars}` : "",
  ]
    .filter(Boolean)
    .join("\n\n");
}

function buildJudgeMessage(input: JudgeInput) {
  const header = buildUserMessage(input.request);
  const candidates = input.candidates
    .map((candidate, index) =>
      [
        `### CANDIDATE ${index + 1} — provider: ${candidate.provider} (${candidate.model}) — compliance score ${candidate.score}/100`,
        candidate.issues.length ? `Issues found by the verifier:\n${candidate.issues.map((issue) => `- ${issue}`).join("\n")}` : "Verifier found no issues.",
        "```json",
        JSON.stringify(candidate.menu),
        "```",
      ].join("\n"),
    )
    .join("\n\n");
  return `${header}\n\n## CANDIDATES\n\n${candidates}\n\nProduce the single best final menu now.`;
}

function buildRepairMessage(input: RepairInput) {
  return [
    buildUserMessage({ ...input.request, exemplars: undefined }),
    "## ISSUES TO FIX",
    input.issues.map((issue) => `- ${issue}`).join("\n"),
    "## CURRENT MENU",
    "```json",
    JSON.stringify(input.menu),
    "```",
    "Return the corrected menu now.",
  ].join("\n\n");
}

function parseDataUrl(dataUrl: string) {
  const match = /^data:(image\/(?:jpeg|png|webp));base64,(.+)$/.exec(dataUrl);
  if (!match) throw new Error("Unsupported reference image.");
  return { mediaType: match[1] as "image/jpeg" | "image/png" | "image/webp", data: match[2] ?? "" };
}

function parseMenuJson(text: string): GeneratedMenuCore {
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error("The engine returned malformed JSON.");
  }
  const wire = menuWireSchema.safeParse(json);
  if (!wire.success) throw new Error("The engine returned an incomplete menu.");
  const strict = generatedMenuSchema.safeParse(wire.data);
  if (!strict.success) throw new Error("The engine returned an empty menu.");
  return strict.data;
}

function parseJudgeJson(text: string): JudgeOutput {
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error("The judge returned malformed JSON.");
  }
  const parsed = judgeWireSchema.safeParse(json);
  if (!parsed.success) throw new Error("The judge returned an incomplete result.");
  if (!generatedMenuSchema.safeParse(parsed.data.menu).success) throw new Error("The judge returned an empty menu.");
  return parsed.data;
}

// ---------- OpenAI-compatible Responses API (Lovable gateway + OpenAI direct) ----------

type ResponsesCall = {
  url: string;
  headers: Record<string, string>;
  model: string;
  instructions: string;
  text: string;
  images?: string[] | undefined;
  schemaName: string;
  schema: unknown;
  signal: AbortSignal;
  stream: boolean;
};

async function responsesError(response: Response, fallback: string) {
  const raw = await response.text().catch(() => "");
  try {
    const parsed = JSON.parse(raw) as { message?: string; error?: { message?: string } };
    return parsed.message ?? parsed.error?.message ?? fallback;
  } catch {
    return raw.trim() || fallback;
  }
}

async function callResponsesApi(call: ResponsesCall): Promise<string> {
  const input = responsesInput(call.text, call.images);
  const response = await fetch(call.url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...call.headers },
    signal: call.signal,
    body: JSON.stringify({
      model: call.model,
      instructions: call.instructions,
      input,
      stream: call.stream,
      reasoning: { effort: "low", summary: "auto" },
      text: { format: { type: "json_schema", name: call.schemaName, strict: true, schema: call.schema } },
    }),
  });
  return readResponsesText(response, call.stream);
}

/** Reads a Responses API reply (JSON body or SSE stream) down to the output text, throwing on API errors. */
async function readResponsesText(response: Response, stream: boolean): Promise<string> {
  if (!response.ok) throw new Error(await responsesError(response, `Engine request failed (${response.status}).`));

  if (!stream) {
    const json = (await response.json()) as {
      status?: string;
      output?: { type: string; content?: { type: string; text?: string; refusal?: string }[] }[];
      error?: { message?: string };
    };
    if (json.error?.message) throw new Error(json.error.message);
    const text = (json.output ?? [])
      .filter((item) => item.type === "message")
      .flatMap((item) => item.content ?? [])
      .map((part) => {
        if (part.type === "refusal") throw new Error(part.refusal ?? "The engine declined this brief.");
        return part.type === "output_text" ? part.text ?? "" : "";
      })
      .join("");
    if (!text) throw new Error("The engine returned an empty response.");
    return text;
  }

  if (!response.body) throw new Error("The engine returned an empty stream.");
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let output = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += chunk.value;
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((value) => value.startsWith("data:"));
      if (!line) continue;
      const raw = line.slice(5).trim();
      if (!raw || raw === "[DONE]") continue;
      const event = JSON.parse(raw) as { type?: string; delta?: string; error?: { message?: string } };
      if (event.type === "error") throw new Error(event.error?.message ?? "Engine stream failed.");
      if (event.type === "response.output_text.delta" && event.delta) output += event.delta;
    }
  }
  if (!output) throw new Error("The engine returned an empty response.");
  return output;
}

function responsesProvider(id: "lovable" | "openai"): Provider {
  const isLovable = id === "lovable";
  const key = () => process.env[isLovable ? "LOVABLE_API_KEY" : "OPENAI_API_KEY"];
  const base = (model: string) => {
    const headers: Record<string, string> = isLovable ? { "Lovable-API-Key": key() ?? "" } : { Authorization: `Bearer ${key() ?? ""}` };
    return { url: isLovable ? LOVABLE_GATEWAY : OPENAI_RESPONSES, headers, model, stream: isLovable };
  };
  return {
    id,
    label: isLovable ? "Halftone (built-in)" : "ChatGPT",
    model: MODELS[id],
    available: () => Boolean(key()),
    generate(request, signal) {
      return withModelFallback(modelChain(id), async (model) =>
        parseMenuJson(await callResponsesApi({ ...base(model), instructions: SYSTEM_PROMPT, text: buildUserMessage(request), images: referenceImagesOf(request), schemaName: "menu_design", schema: MENU_JSON_SCHEMA, signal })),
      );
    },
    judge(input, signal) {
      return withModelFallback(modelChain(id), async (model) =>
        parseJudgeJson(await callResponsesApi({ ...base(model), instructions: JUDGE_SYSTEM_PROMPT, text: buildJudgeMessage(input), images: referenceImagesOf(input.request), schemaName: "menu_judgement", schema: JUDGE_JSON_SCHEMA, signal })),
      );
    },
    repair(input, signal) {
      return withModelFallback(modelChain(id), async (model) =>
        parseMenuJson(await callResponsesApi({ ...base(model), instructions: REPAIR_SYSTEM_PROMPT, text: buildRepairMessage(input), images: referenceImagesOf(input.request), schemaName: "menu_design", schema: MENU_JSON_SCHEMA, signal })),
      );
    },
  };
}

// ---------- Claude via the official Anthropic SDK ----------

let anthropicClient: Anthropic | undefined;
function anthropic() {
  if (!anthropicClient) anthropicClient = new Anthropic({ apiKey: process.env["ANTHROPIC_API_KEY"], maxRetries: 1 });
  return anthropicClient;
}

function claudeContent(text: string, images: string[] = []): Anthropic.ContentBlockParam[] {
  const blocks: Anthropic.ContentBlockParam[] = [];
  for (const image of images) {
    const { mediaType, data } = parseDataUrl(image);
    blocks.push({ type: "image", source: { type: "base64", media_type: mediaType, data } });
  }
  blocks.push({ type: "text", text });
  return blocks;
}

const claudeProvider: Provider = {
  id: "claude",
  label: "Claude",
  model: MODELS.claude,
  available: () => Boolean(process.env["ANTHROPIC_API_KEY"]),
  generate(request, signal) {
    return withModelFallback(modelChain("claude"), async (model) => {
      const response = await anthropic().messages.parse(
        {
          model,
          max_tokens: 16000,
          system: [{ type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
          messages: [{ role: "user", content: claudeContent(buildUserMessage(request), referenceImagesOf(request)) }],
          output_config: { format: zodOutputFormat(menuWireSchema), effort: "medium" },
        },
        { signal },
      );
      if (response.stop_reason === "refusal") throw new Error("Claude declined this brief.");
      if (response.stop_reason === "max_tokens") throw new Error("Claude ran out of room; shorten the brief.");
      if (!response.parsed_output) throw new Error("Claude returned an incomplete menu.");
      const strict = generatedMenuSchema.safeParse(response.parsed_output);
      if (!strict.success) throw new Error("Claude returned an empty menu.");
      return strict.data;
    });
  },
  judge(input, signal) {
    return withModelFallback(modelChain("claude"), async (model) => {
      const response = await anthropic().messages.parse(
        {
          model,
          max_tokens: 16000,
          system: [{ type: "text", text: JUDGE_SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
          messages: [{ role: "user", content: claudeContent(buildJudgeMessage(input), referenceImagesOf(input.request)) }],
          output_config: { format: zodOutputFormat(judgeWireSchema), effort: "high" },
        },
        { signal },
      );
      if (response.stop_reason === "refusal") throw new Error("Claude declined to judge this brief.");
      if (!response.parsed_output) throw new Error("Claude returned an incomplete judgement.");
      if (!generatedMenuSchema.safeParse(response.parsed_output.menu).success) throw new Error("Claude returned an empty judged menu.");
      return response.parsed_output;
    });
  },
  repair(input, signal) {
    return withModelFallback(modelChain("claude"), async (model) => {
      const response = await anthropic().messages.parse(
        {
          model,
          max_tokens: 16000,
          system: [{ type: "text", text: REPAIR_SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
          messages: [{ role: "user", content: claudeContent(buildRepairMessage(input), referenceImagesOf(input.request)) }],
          output_config: { format: zodOutputFormat(menuWireSchema), effort: "low" },
        },
        { signal },
      );
      if (response.stop_reason === "refusal") throw new Error("Claude declined to repair this menu.");
      if (!response.parsed_output) throw new Error("Claude returned an incomplete repair.");
      const strict = generatedMenuSchema.safeParse(response.parsed_output);
      if (!strict.success) throw new Error("Claude returned an empty repaired menu.");
      return strict.data;
    });
  },
};

type ChatCall = { url: string; headers: Record<string, string>; model: string; instructions: string; text: string; images?: string[] | undefined; schemaName: string; schema: unknown; signal: AbortSignal };

/** OpenAI-compatible chat completions with a JSON schema response (Gemini direct, or google/* models on the Lovable gateway). */
async function callChatCompletions(call: ChatCall): Promise<string> {
  // The schema also travels in the text, so a gateway that ignores response_format still answers in the right shape.
  const prompt = `${call.text}\n\n## OUTPUT JSON SCHEMA (answer with one JSON object of exactly this shape, no prose, no code fences)\n${JSON.stringify(call.schema)}`;
  const content: unknown[] = [{ type: "text", text: prompt }, ...(call.images ?? []).map((image) => ({ type: "image_url", image_url: { url: image } }))];
  const response = await fetch(call.url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...call.headers },
    signal: call.signal,
    body: JSON.stringify({
      model: call.model,
      messages: [{ role: "system", content: call.instructions }, { role: "user", content }],
      response_format: { type: "json_schema", json_schema: { name: call.schemaName, strict: true, schema: call.schema } },
      stream: false,
    }),
  });
  if (!response.ok) throw new Error(await responsesError(response, `Engine request failed (${response.status}).`));
  const json = (await response.json()) as { choices?: { message?: { content?: string | { type: string; text?: string }[]; refusal?: string | null } }[]; error?: { message?: string } };
  if (json.error?.message) throw new Error(json.error.message);
  const message = json.choices?.[0]?.message;
  if (message?.refusal) throw new Error(message.refusal);
  const text = typeof message?.content === "string" ? message.content : (message?.content ?? []).map((part) => part.text ?? "").join("");
  const cleaned = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "");
  if (!cleaned) throw new Error("The engine returned an empty response.");
  return cleaned;
}

function geminiBase(model: string) {
  const direct = process.env["GEMINI_API_KEY"];
  return direct
    ? { url: GEMINI_CHAT, headers: { Authorization: `Bearer ${direct}` } as Record<string, string>, model }
    : { url: LOVABLE_GATEWAY_CHAT, headers: { "Lovable-API-Key": process.env["LOVABLE_API_KEY"] ?? "" } as Record<string, string>, model };
}

/**
 * Google Gemini as a fourth engine. With GEMINI_API_KEY it calls Google directly;
 * otherwise it uses the Lovable gateway's google/* models with the existing
 * LOVABLE_API_KEY (set HALFTONE_GATEWAY_GEMINI=off to keep gateway usage down).
 */
const geminiProvider: Provider = {
  id: "gemini",
  label: "Google Gemini",
  model: MODELS.gemini,
  available: () => Boolean(process.env["GEMINI_API_KEY"]) || (Boolean(process.env["LOVABLE_API_KEY"]) && process.env["HALFTONE_GATEWAY_GEMINI"] !== "off"),
  generate(request, signal) {
    return withModelFallback(modelChain("gemini"), async (model) =>
      parseMenuJson(await callChatCompletions({ ...geminiBase(model), instructions: SYSTEM_PROMPT, text: buildUserMessage(request), images: referenceImagesOf(request), schemaName: "menu_design", schema: MENU_JSON_SCHEMA, signal })),
    );
  },
  judge(input, signal) {
    return withModelFallback(modelChain("gemini"), async (model) =>
      parseJudgeJson(await callChatCompletions({ ...geminiBase(model), instructions: JUDGE_SYSTEM_PROMPT, text: buildJudgeMessage(input), images: referenceImagesOf(input.request), schemaName: "menu_judgement", schema: JUDGE_JSON_SCHEMA, signal })),
    );
  },
  repair(input, signal) {
    return withModelFallback(modelChain("gemini"), async (model) =>
      parseMenuJson(await callChatCompletions({ ...geminiBase(model), instructions: REPAIR_SYSTEM_PROMPT, text: buildRepairMessage(input), images: referenceImagesOf(input.request), schemaName: "menu_design", schema: MENU_JSON_SCHEMA, signal })),
    );
  },
};

/**
 * Processing sequence: every available engine generates in parallel (Halftone
 * built-in, ChatGPT, Claude, Gemini) → deterministic verifier → judge (Claude,
 * then ChatGPT, then Gemini, then built-in) merges → repair → fit → verify.
 */
export const PROVIDERS: Provider[] = [responsesProvider("lovable"), responsesProvider("openai"), claudeProvider, geminiProvider];

export type StructuredCall = {
  system: string;
  text: string;
  /** data:image/...;base64,... URLs; all engines here accept images. */
  images?: string[] | undefined;
  schemaName: string;
  jsonSchema: unknown;
  signal: AbortSignal;
  /** Provider order; defaults to openai → lovable → claude (vision quality first, built-in second). */
  order?: ProviderId[] | undefined;
};

function responsesInput(text: string, images: string[] | undefined) {
  if (!images?.length) return text;
  return [{ role: "user", content: [{ type: "input_text", text }, ...images.map((image) => ({ type: "input_image", image_url: image }))] }];
}

/**
 * One structured-output call against any schema (used for store analysis and
 * display measurement). Tries providers in order, each with its model chain;
 * returns the raw JSON text and which provider/model answered.
 */
export async function structuredJson(call: StructuredCall): Promise<{ text: string; provider: ProviderId; model: string }> {
  const order = call.order ?? ["openai", "gemini", "lovable", "claude"];
  const live = availableProviders();
  const errors: string[] = [];
  for (const id of order) {
    const provider = live.find((candidate) => candidate.id === id);
    if (!provider) continue;
    try {
      if (id === "claude") {
        const served = await withModelFallback(modelChain("claude"), async (model) => {
          const content: Anthropic.ContentBlockParam[] = [];
          for (const image of call.images ?? []) {
            const { mediaType, data } = parseDataUrl(image);
            content.push({ type: "image", source: { type: "base64", media_type: mediaType, data } });
          }
          content.push({ type: "text", text: call.text });
          const response = await anthropic().messages.create(
            {
              model,
              max_tokens: 8000,
              system: call.system,
              messages: [{ role: "user", content }],
              output_config: { format: { type: "json_schema", schema: call.jsonSchema as Record<string, unknown> }, effort: "low" },
            },
            { signal: call.signal },
          );
          if (response.stop_reason === "refusal") throw new Error("Claude declined this analysis.");
          const text = response.content.map((block) => (block.type === "text" ? block.text : "")).join("");
          if (!text) throw new Error("Claude returned an empty analysis.");
          return text;
        });
        return { text: served.value, provider: id, model: served.model };
      }
      const isLovable = id === "lovable";
      const key = process.env[isLovable ? "LOVABLE_API_KEY" : "OPENAI_API_KEY"] ?? "";
      const headers: Record<string, string> = isLovable ? { "Lovable-API-Key": key } : { Authorization: `Bearer ${key}` };
      if (id === "gemini") {
        const served = await withModelFallback(modelChain("gemini"), async (model) =>
          callChatCompletions({ ...geminiBase(model), instructions: call.system, text: call.text, images: call.images, schemaName: call.schemaName, schema: call.jsonSchema, signal: call.signal }),
        );
        return { text: served.value, provider: "gemini", model: served.model };
      }
      const served = await withModelFallback(modelChain(id), async (model) => {
        const response = await fetch(isLovable ? LOVABLE_GATEWAY : OPENAI_RESPONSES, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...headers },
          signal: call.signal,
          body: JSON.stringify({
            model,
            instructions: call.system,
            input: responsesInput(call.text, call.images),
            stream: isLovable,
            reasoning: { effort: "low", summary: "auto" },
            text: { format: { type: "json_schema", name: call.schemaName, strict: true, schema: call.jsonSchema } },
          }),
        });
        return readResponsesText(response, isLovable);
      });
      return { text: served.value, provider: id, model: served.model };
    } catch (error) {
      errors.push(`${provider.label}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  throw new Error(errors.length ? `Analysis failed. ${errors.join(" · ")}` : "AI is not configured yet.");
}

export function availableProviders() {
  return PROVIDERS.filter((provider) => provider.available());
}

/**
 * Judge preference: HALFTONE_JUDGE env → learned ranking from memory (best average
 * verified score, min 5 samples) → Claude → ChatGPT → built-in.
 */
export function pickJudge(exclude: ProviderId[] = [], learned: ProviderId[] = []) {
  const preferred = process.env["HALFTONE_JUDGE"] as ProviderId | undefined;
  const order: ProviderId[] = [...(preferred ? [preferred] : []), ...learned, "claude", "openai", "gemini", "lovable"];
  const live = availableProviders();
  for (const id of order) {
    const provider = live.find((candidate) => candidate.id === id);
    if (provider && !exclude.includes(id)) return provider;
  }
  return live.find((provider) => !exclude.includes(provider.id)) ?? live[0];
}
