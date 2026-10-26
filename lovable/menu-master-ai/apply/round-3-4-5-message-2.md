Rounds 3-5 upgrade, MESSAGE 2 of 3 (server libraries). The build will still fail until message 3 is applied; that is expected — just write the files.

Apply EXACTLY as given; overwrite/create each file verbatim; do not refactor, rename or "improve"; keep your explanation to a few lines. Do NOT touch src/lib/image-gateway.server.ts, src/routes/api/generate-image.ts, src/routes/api/edit-image.ts or src/lib/streamImage.ts. The Supabase tables public.halftone_stores, public.halftone_displays and the new columns on public.halftone_generations ALREADY EXIST (RLS enabled) — do not create, alter or migrate them.


===== FILE: src/lib/ai/memory.server.ts =====
```tsx
import type { SupabaseClient } from "@supabase/supabase-js";

import type { EngineReport, GeneratedMenuCore, MenuRequest, ProviderId } from "./schema";

/**
 * Halftone learning memory (Supabase table `halftone_generations`).
 *
 * Every generation is recorded with its engine scores. Two things are read
 * back on later runs:
 *   1. Exemplars — past menus that scored 100/100 for the same mode and
 *      cuisine, shown to the engines as "approved output" (few-shot).
 *   2. Judge ranking — which judge model produced the best verified results,
 *      so the ensemble promotes it automatically.
 *
 * Every call is guarded: memory can never break or slow a generation beyond
 * a short timeout.
 */

const TIMEOUT_MS = Number(process.env["HALFTONE_MEMORY_TIMEOUT_MS"] ?? 2500);
const MIN_SAMPLES = 5;

type Row = {
  id?: string;
  created_at?: string;
  brief: string;
  mode: string;
  style: string;
  size: string;
  pages: string;
  cuisine: string | null;
  is_revision: boolean;
  had_reference_image: boolean;
  strategy: string;
  judge_provider: string | null;
  winner: string | null;
  final_score: number;
  final_issues: string[];
  candidates: unknown;
  menu: GeneratedMenuCore;
  duration_ms: number | null;
  store_id?: string | null;
  display_id?: string | null;
  template_id?: string | null;
};

async function db(): Promise<SupabaseClient | undefined> {
  if (process.env["HALFTONE_MEMORY"] === "off") return undefined;
  if (!process.env["SUPABASE_URL"] || !process.env["SUPABASE_SERVICE_ROLE_KEY"]) return undefined;
  try {
    const mod = await import("@/integrations/supabase/client.server");
    return mod.supabaseAdmin as unknown as SupabaseClient;
  } catch {
    return undefined;
  }
}

/** Resolves to undefined on timeout or error so memory can never break a generation. */
function withTimeout<T>(promise: PromiseLike<T>): Promise<T | undefined> {
  return new Promise<T | undefined>((resolve) => {
    const timer = setTimeout(() => resolve(undefined), TIMEOUT_MS);
    Promise.resolve(promise).then(
      (value) => { clearTimeout(timer); resolve(value); },
      () => { clearTimeout(timer); resolve(undefined); },
    );
  });
}

export type MemoryContext = {
  exemplars: string;
  exemplarCount: number;
  judgeRanking: ProviderId[];
  totalRecorded: number;
};

const EMPTY: MemoryContext = { exemplars: "", exemplarCount: 0, judgeRanking: [], totalRecorded: 0 };

/** Loads exemplars and the judge ranking for this request. Never throws. */
export async function loadMemory(request: MenuRequest, cuisines: string[]): Promise<MemoryContext> {
  const client = await db();
  if (!client) return EMPTY;
  const cuisine = cuisines[0] ?? null;

  const exemplarQuery = client
    .from("halftone_generations")
    .select("brief, menu, final_score, created_at")
    .eq("mode", request.mode)
    .eq("final_score", 100)
    .order("created_at", { ascending: false })
    .limit(2);
  const exemplarsPromise = withTimeout(cuisine ? exemplarQuery.eq("cuisine", cuisine) : exemplarQuery.is("cuisine", null));
  const statsPromise = withTimeout(
    client.from("halftone_generations").select("judge_provider, final_score, strategy").not("judge_provider", "is", null).order("created_at", { ascending: false }).limit(60),
  );
  const countPromise = withTimeout(client.from("halftone_generations").select("id", { count: "exact", head: true }));

  const [exemplars, stats, total] = await Promise.all([exemplarsPromise, statsPromise, countPromise]);

  const rows = ((exemplars?.data ?? []) as Pick<Row, "brief" | "menu" | "final_score" | "created_at">[]).filter((row) => row.brief.trim() !== request.brief.trim());
  const exemplarText = rows.length
    ? rows
        .map((row, index) => `### APPROVED EXAMPLE ${index + 1} (scored 100/100 on its own brief)\nBRIEF: ${row.brief.slice(0, 600)}\nOUTPUT: ${JSON.stringify(stripForExemplar(row.menu)).slice(0, 3500)}`)
        .join("\n\n")
    : "";

  const byJudge = new Map<string, { sum: number; n: number }>();
  for (const row of (stats?.data ?? []) as { judge_provider: string | null; final_score: number; strategy: string }[]) {
    if (!row.judge_provider || row.strategy !== "judged-merge") continue;
    const entry = byJudge.get(row.judge_provider) ?? { sum: 0, n: 0 };
    entry.sum += row.final_score;
    entry.n += 1;
    byJudge.set(row.judge_provider, entry);
  }
  const ranking = Array.from(byJudge.entries())
    .filter(([, value]) => value.n >= MIN_SAMPLES)
    .sort((a, b) => b[1].sum / b[1].n - a[1].sum / a[1].n)
    .map(([key]) => key as ProviderId);

  return { exemplars: exemplarText, exemplarCount: rows.length, judgeRanking: ranking, totalRecorded: total?.count ?? 0 };
}

function stripForExemplar(menu: GeneratedMenuCore) {
  // Keep structure and copy; drop long photo prompts to save tokens.
  return {
    ...menu,
    heroPhoto: { label: menu.heroPhoto.label, prompt: "…" },
    dishPhotos: menu.dishPhotos.map((photo) => ({ label: photo.label, prompt: "…" })),
    sections: menu.sections.map((section) => ({ ...section, items: section.items.map((item) => ({ ...item, photoPrompt: "…" })) })),
  };
}

/** Records a finished generation. Fire-and-forget; never throws. */
export async function recordGeneration(request: MenuRequest, cuisines: string[], menu: GeneratedMenuCore, engine: EngineReport, durationMs: number): Promise<void> {
  const client = await db();
  if (!client) return;
  const row: Row = {
    brief: request.brief,
    mode: request.mode,
    style: request.style,
    size: request.size,
    pages: request.pages,
    cuisine: cuisines[0] ?? null,
    is_revision: Boolean(request.previous),
    had_reference_image: Boolean(request.referenceImage),
    strategy: engine.strategy,
    judge_provider: engine.judge?.provider ?? null,
    winner: engine.judge?.winner ?? engine.candidates.find((candidate) => candidate.status === "ok")?.provider ?? null,
    final_score: engine.finalScore,
    final_issues: engine.finalIssues,
    candidates: engine.candidates,
    menu,
    duration_ms: durationMs,
    store_id: request.storeId ?? null,
    display_id: request.displayId ?? null,
    template_id: request.template ?? null,
  };
  await withTimeout(client.from("halftone_generations").insert(row));
}

```

===== FILE: src/lib/ai/providers.server.ts =====
```tsx
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
const OPENAI_RESPONSES = "https://api.openai.com/v1/responses";

const MODELS = {
  lovable: process.env["LOVABLE_TEXT_MODEL"] ?? "openai/gpt-6-astra",
  openai: process.env["OPENAI_MODEL"] ?? "gpt-6-astra",
  claude: process.env["ANTHROPIC_MODEL"] ?? "claude-opus-5-5",
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
  lovable: list(process.env["LOVABLE_FALLBACK_MODELS"], ["openai/gpt-6.1-sol", "openai/gpt-6-sol", "openai/gpt-5.6-sol"]),
  openai: list(process.env["OPENAI_FALLBACK_MODELS"], ["gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol", "gpt-5.5"]),
  claude: list(process.env["ANTHROPIC_FALLBACK_MODELS"], ["claude-sonnet-5-5", "claude-opus-5"]),
};

export function modelChain(id: ProviderId) {
  return Array.from(new Set([MODELS[id], ...FALLBACK_MODELS[id]]));
}

/** True when an error is about the model id itself (unknown, retired, not permitted), not about the request. */
export function isModelError(message: string) {
  const lower = message.toLowerCase();
  if (/abort|timed out|timeout/.test(lower)) return false;
  return /model/.test(lower) && /not[ _](found|exist|available|supported)|does not exist|unknown|invalid|deprecat|retired|decommission|shut ?down|no longer|permission|access|\b404\b/.test(lower);
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

export function buildUserMessage(request: MenuRequest) {
  return [
    `BRIEF:\n${request.brief}`,
    `MODE: ${request.mode.toUpperCase()}`,
    `TYPE STYLE: ${request.style}`,
    `PAGE COUNT: ${request.pages}`,
    request.display ? displayBriefLines(request.display).join("\n") : `FORMAT: ${request.size} — ${describeFormat(request.size)}`,
    request.store ? `VENUE PROFILE: ${request.store.venueName || "unnamed venue"} — ${request.store.storeType.replace("-", " ")}, ${request.store.priceTier} price point${request.store.cuisine ? `, ${request.store.cuisine}` : ""}${request.store.audience ? `, audience: ${request.store.audience}` : ""}. Copy voice: ${request.store.voice}` : "",
    request.template && templateById(request.template) ? describeTemplate(templateById(request.template)!) : "",
    request.cuisine?.length ? `DETECTED CUISINE: ${request.cuisine.join(" + ")}. Apply these plating cues to every photo brief:\n${cuisineCues(request.cuisine).map((line) => `- ${line}`).join("\n")}` : "",
    request.referenceImage ? "A photo or screenshot of the existing menu is attached. Transcribe it faithfully, then redesign; the written brief wins on any conflict." : "",
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
  image?: string | undefined;
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
  const input = call.image
    ? [{ role: "user", content: [{ type: "input_text", text: call.text }, { type: "input_image", image_url: call.image }] }]
    : call.text;
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
        parseMenuJson(await callResponsesApi({ ...base(model), instructions: SYSTEM_PROMPT, text: buildUserMessage(request), image: request.referenceImage, schemaName: "menu_design", schema: MENU_JSON_SCHEMA, signal })),
      );
    },
    judge(input, signal) {
      return withModelFallback(modelChain(id), async (model) =>
        parseJudgeJson(await callResponsesApi({ ...base(model), instructions: JUDGE_SYSTEM_PROMPT, text: buildJudgeMessage(input), image: input.request.referenceImage, schemaName: "menu_judgement", schema: JUDGE_JSON_SCHEMA, signal })),
      );
    },
    repair(input, signal) {
      return withModelFallback(modelChain(id), async (model) =>
        parseMenuJson(await callResponsesApi({ ...base(model), instructions: REPAIR_SYSTEM_PROMPT, text: buildRepairMessage(input), image: input.request.referenceImage, schemaName: "menu_design", schema: MENU_JSON_SCHEMA, signal })),
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

function claudeContent(text: string, image?: string): Anthropic.ContentBlockParam[] {
  const blocks: Anthropic.ContentBlockParam[] = [];
  if (image) {
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
          messages: [{ role: "user", content: claudeContent(buildUserMessage(request), request.referenceImage) }],
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
          messages: [{ role: "user", content: claudeContent(buildJudgeMessage(input), input.request.referenceImage) }],
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
          messages: [{ role: "user", content: claudeContent(buildRepairMessage(input), input.request.referenceImage) }],
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

export const PROVIDERS: Provider[] = [responsesProvider("lovable"), responsesProvider("openai"), claudeProvider];

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
  const order = call.order ?? ["openai", "lovable", "claude"];
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
  const order: ProviderId[] = [...(preferred ? [preferred] : []), ...learned, "claude", "openai", "lovable"];
  const live = availableProviders();
  for (const id of order) {
    const provider = live.find((candidate) => candidate.id === id);
    if (provider && !exclude.includes(id)) return provider;
  }
  return live.find((provider) => !exclude.includes(provider.id)) ?? live[0];
}

```

===== FILE: src/lib/ai/ensemble.server.ts =====
```tsx
import { detectCuisine, enrichMenuPhotos } from "./cuisine";
import { fitMenuToDisplay } from "./display";
import { loadMemory, recordGeneration } from "./memory.server";
import { availableProviders, pickJudge, type Provider } from "./providers.server";
import type { EngineCandidate, EngineReport, GeneratedMenu, GeneratedMenuCore, MenuRequest } from "./schema";
import { verifyMenu } from "./verify";

/**
 * Halftone ensemble.
 *
 *   brief ─► cuisine detection + learning memory (exemplars, judge ranking)
 *         ─┬─► Halftone built-in (Lovable gateway) ─┐
 *          ├─► ChatGPT (OpenAI)                    ├─► verifier ─► judge (merge + fix) ─► verifier
 *          └─► Claude (Anthropic)                  ┘                                        │
 *                                                        repair pass (only if issues remain) ◄┘
 *                                                        ─► photo-prompt enrichment ─► record to memory ─► result
 *
 * All engines run in parallel. Any engine can fail or be unconfigured; the
 * app always returns the best menu it could produce. A judged merge or a
 * repair is only kept when it verifies at least as well as what it replaces.
 */

const GENERATE_TIMEOUT_MS = Number(process.env["HALFTONE_GENERATE_TIMEOUT_MS"] ?? 110_000);
const JUDGE_TIMEOUT_MS = Number(process.env["HALFTONE_JUDGE_TIMEOUT_MS"] ?? 90_000);
const REPAIR_TIMEOUT_MS = Number(process.env["HALFTONE_REPAIR_TIMEOUT_MS"] ?? 60_000);
const PERFECT = 100;

type Attempt = { provider: Provider; candidate: EngineCandidate; menu?: GeneratedMenuCore };

function timed<T>(ms: number, run: (signal: AbortSignal) => Promise<T>): Promise<{ value?: T; error?: string; ms: number }> {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return run(controller.signal)
    .then((value) => ({ value, ms: Date.now() - started }))
    .catch((error: unknown) => ({ error: controller.signal.aborted ? "Timed out." : error instanceof Error ? error.message : "Failed.", ms: Date.now() - started }))
    .finally(() => clearTimeout(timer));
}

async function runProvider(provider: Provider, request: MenuRequest, cuisine: string[]): Promise<Attempt> {
  const result = await timed(GENERATE_TIMEOUT_MS, (signal) => provider.generate(request, signal));
  if (result.value) {
    // Enrich before scoring so engines are judged on content, not on prompt length the enrichment fixes for free.
    const menu = enrichMenuPhotos(result.value.value, cuisine);
    const verification = verifyMenu(menu, request);
    return {
      provider,
      menu,
      candidate: { provider: provider.id, model: result.value.model, status: "ok", score: verification.score, issues: verification.issues, ms: result.ms },
    };
  }
  return { provider, candidate: { provider: provider.id, model: provider.model, status: "failed", score: 0, issues: [], error: result.error ?? "Engine failed.", ms: result.ms } };
}

export async function composeMenu(input: MenuRequest): Promise<GeneratedMenu> {
  const startedAt = Date.now();
  const providers = availableProviders();
  if (providers.length === 0) throw new Error("AI is not configured yet. Add LOVABLE_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY.");

  // 1. Deterministic context: cuisine + what the memory has learned.
  const cuisine = detectCuisine(input.brief);
  const memory = await loadMemory(input, cuisine);
  const request: MenuRequest = { ...input, cuisine, exemplars: memory.exemplars || undefined };

  // 2. Every engine at once.
  const attempts = await Promise.all(providers.map((provider) => runProvider(provider, request, cuisine)));
  const successes = attempts.filter((attempt): attempt is Attempt & { menu: GeneratedMenuCore } => Boolean(attempt.menu));
  const candidates = attempts.map((attempt) => attempt.candidate);
  if (successes.length === 0) {
    const reasons = attempts.map((attempt) => `${attempt.provider.label}: ${attempt.candidate.error ?? "failed"}`).join(" · ");
    throw new Error(`Every engine failed. ${reasons}`);
  }
  successes.sort((a, b) => b.candidate.score - a.candidate.score || a.candidate.ms - b.candidate.ms);
  const best = successes[0]!;

  let menu: GeneratedMenuCore = best.menu;
  let score = best.candidate.score;
  let issues = best.candidate.issues;
  const report: EngineReport = {
    strategy: successes.length === 1 ? "single" : "best-candidate",
    candidates,
    cuisine,
    memory: { exemplars: memory.exemplarCount, totalRecorded: memory.totalRecorded, judgeRanking: memory.judgeRanking },
    finalScore: score,
    finalIssues: issues,
    totalMs: 0,
  };

  // 3. Judge merges when there is more than one candidate.
  const judge = pickJudge([], memory.judgeRanking);
  if (successes.length > 1 && judge) {
    const result = await timed(JUDGE_TIMEOUT_MS, (signal) =>
      judge.judge(
        { request, candidates: successes.map((attempt) => ({ provider: attempt.provider.id, model: attempt.provider.model, menu: attempt.menu, score: attempt.candidate.score, issues: attempt.candidate.issues })) },
        signal,
      ),
    );
    if (result.value) {
      const judged = { ...result.value.value, menu: enrichMenuPhotos(result.value.value.menu, cuisine) };
      const verification = verifyMenu(judged.menu, request);
      const keep = verification.score >= score || (score < PERFECT && verification.score >= score - 5);
      report.judge = {
        provider: judge.id,
        model: result.value.model,
        winner: judged.winner,
        rationale: keep ? judged.rationale : `${judged.rationale} (Merge scored ${verification.score}, below the best candidate at ${score}, so the best candidate was kept.)`,
        mergedFrom: judged.mergedFrom,
        ms: result.ms,
      };
      if (keep) {
        report.strategy = "judged-merge";
        menu = judged.menu;
        score = verification.score;
        issues = verification.issues;
      }
    } else {
      report.judge = { provider: judge.id, model: judge.model, winner: best.provider.id, rationale: `Judge unavailable (${result.error}); best verified candidate used.`, mergedFrom: [best.provider.id], ms: result.ms };
    }
  }

  // 4. One targeted repair pass if the verifier still lists issues.
  const repairer = judge ?? providers[0];
  if (issues.length > 0 && score < PERFECT && repairer) {
    const result = await timed(REPAIR_TIMEOUT_MS, (signal) => repairer.repair({ request, menu, issues }, signal));
    if (result.value) {
      const repaired = enrichMenuPhotos(result.value.value, cuisine);
      const verification = verifyMenu(repaired, request);
      const applied = verification.score > score;
      report.repair = { provider: repairer.id, model: result.value.model, before: score, after: verification.score, applied, ms: result.ms };
      if (applied) {
        menu = repaired;
        score = verification.score;
        issues = verification.issues;
      }
    } else {
      report.repair = { provider: repairer.id, model: repairer.model, before: score, after: score, applied: false, ms: result.ms };
    }
  }

  // 5. Fit to the measured display (deterministic), then final verification.
  if (request.display) {
    const fit = fitMenuToDisplay(menu, request.display);
    menu = fit.menu;
    report.fit = { format: request.display.formatLabel, omitted: fit.omitted, trimmedDescriptions: fit.trimmedDescriptions };
  }
  if (request.template) report.template = request.template;
  menu = enrichMenuPhotos(menu, cuisine);
  const finalVerification = verifyMenu(menu, request);
  report.finalScore = finalVerification.score;
  report.finalIssues = finalVerification.issues;
  report.totalMs = Date.now() - startedAt;

  // 6. Learn from it (never blocks the response on failure).
  await recordGeneration(input, cuisine, menu, report, report.totalMs);

  return { ...menu, engine: report };
}

```

===== FILE: src/lib/ai/store.server.ts =====
```tsx
import type { SupabaseClient } from "@supabase/supabase-js";

import { DISPLAY_MEASUREMENT_JSON_SCHEMA, DISPLAY_MEASUREMENT_PROMPT, displayMeasurementSchema, resolveDisplay, type DisplayMeasurement, type DisplaySpec } from "./display";
import { availableProviders, structuredJson } from "./providers.server";
import { mergeProfiles, profileFromText, STORE_ANALYSIS_PROMPT, STORE_PROFILE_JSON_SCHEMA, storeProfileSchema, type StoreProfile } from "./store";
import { recommendTemplates, type TemplateMatch } from "./templates";

/**
 * Server side of the store wizard. Both steps degrade gracefully: with no AI
 * key the profile comes from the text baseline and the display from the
 * user-entered size; with a key, a vision engine fills in the rest.
 */

const ANALYSIS_TIMEOUT_MS = Number(process.env["HALFTONE_ANALYSIS_TIMEOUT_MS"] ?? 90_000);

async function db(): Promise<SupabaseClient | undefined> {
  if (process.env["HALFTONE_MEMORY"] === "off") return undefined;
  if (!process.env["SUPABASE_URL"] || !process.env["SUPABASE_SERVICE_ROLE_KEY"]) return undefined;
  try {
    const mod = await import("@/integrations/supabase/client.server");
    return mod.supabaseAdmin as unknown as SupabaseClient;
  } catch {
    return undefined;
  }
}

function signal(ms: number) {
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms).unref?.();
  return controller.signal;
}

function parseJson<T>(text: string, parse: (json: unknown) => T): T | undefined {
  try {
    return parse(JSON.parse(text));
  } catch {
    return undefined;
  }
}

export async function analyzeStoreOnServer(input: { description: string; venueName?: string | undefined; images?: string[] | undefined }) {
  const warnings: string[] = [];
  const baseline = profileFromText(input.description, input.venueName ?? "");
  let profile: StoreProfile = baseline;
  let engine: { provider: string; model: string } | null = null;

  if (availableProviders().length === 0) {
    warnings.push("No AI engine is configured; the profile comes from the written description only.");
  } else {
    try {
      const result = await structuredJson({
        system: STORE_ANALYSIS_PROMPT,
        text: [`DESCRIPTION:\n${input.description}`, input.venueName ? `VENUE NAME: ${input.venueName}` : "", input.images?.length ? `${input.images.length} photo(s) attached.` : "No photos attached."].filter(Boolean).join("\n\n"),
        images: input.images,
        schemaName: "store_profile",
        jsonSchema: STORE_PROFILE_JSON_SCHEMA,
        signal: signal(ANALYSIS_TIMEOUT_MS),
      });
      const parsed = parseJson(result.text, (json) => storeProfileSchema.partial().parse(json));
      if (parsed) {
        profile = mergeProfiles(baseline, parsed);
        engine = { provider: result.provider, model: result.model };
      } else {
        warnings.push("The engine returned an unreadable profile; using the description-only profile.");
      }
    } catch (error) {
      warnings.push(`Store analysis fell back to the description only (${error instanceof Error ? error.message : "engine error"}).`);
    }
  }
  if (!profile.palette.length && (input.images?.length ?? 0) === 0) warnings.push("Add a storefront or signage photo to detect brand colours.");

  const templates: TemplateMatch[] = recommendTemplates(profile, 4);
  let storeId: string | undefined;
  const client = await db();
  if (client) {
    try {
      const inserted = await client
        .from("halftone_stores")
        .insert({
          description: input.description,
          photo_count: input.images?.length ?? 0,
          profile,
          recommended_templates: templates.map((match) => ({ id: match.template.id, score: match.score, reasons: match.reasons })),
          engine_provider: engine?.provider ?? null,
          engine_model: engine?.model ?? null,
          confidence: profile.confidence,
        })
        .select("id")
        .single();
      storeId = (inserted.data as { id?: string } | null)?.id;
    } catch {
      /* memory is best-effort */
    }
  }
  return { storeId, profile, templates, engine, warnings };
}

export async function measureDisplayOnServer(input: { image: string; imageAspect?: number | undefined; widthCm?: number | undefined; heightCm?: number | undefined; viewingDistanceM?: number | undefined; storeId?: string | undefined; hint?: string | undefined }) {
  const warnings: string[] = [];
  let measurement: DisplayMeasurement | undefined;
  let engine: { provider: string; model: string } | null = null;

  if (availableProviders().length === 0) {
    warnings.push("No AI engine is configured; enter the display width and height to fit the menu.");
  } else {
    try {
      const result = await structuredJson({
        system: DISPLAY_MEASUREMENT_PROMPT,
        text: [
          "Measure the display in the attached photo.",
          input.hint ? `OWNER'S HINT: ${input.hint}` : "",
          input.widthCm ? `The owner says the width is ${input.widthCm} cm.` : "",
          input.heightCm ? `The owner says the height is ${input.heightCm} cm.` : "",
        ].filter(Boolean).join("\n"),
        images: [input.image],
        schemaName: "display_measurement",
        jsonSchema: DISPLAY_MEASUREMENT_JSON_SCHEMA,
        signal: signal(ANALYSIS_TIMEOUT_MS),
      });
      measurement = parseJson(result.text, (json) => displayMeasurementSchema.parse(json));
      if (measurement) engine = { provider: result.provider, model: result.model };
      else warnings.push("The engine returned an unreadable measurement; using your entered size.");
    } catch (error) {
      warnings.push(`Display measurement fell back to your entered size (${error instanceof Error ? error.message : "engine error"}).`);
    }
  }

  const spec: DisplaySpec = resolveDisplay(measurement, { widthCm: input.widthCm, heightCm: input.heightCm, viewingDistanceM: input.viewingDistanceM, imageAspect: input.imageAspect });
  if (spec.sizeSource === "model" && spec.sizeConfidence < 0.5) warnings.push("The size estimate is low-confidence. Enter the real width or height for exact fitting.");
  if (spec.sizeSource === "aspect-only") warnings.push("Only the shape of the display was detected. Enter the real width or height for exact fitting.");

  let displayId: string | undefined;
  const client = await db();
  if (client) {
    try {
      const inserted = await client
        .from("halftone_displays")
        .insert({
          store_id: input.storeId ?? null,
          measurement: measurement ?? {},
          spec,
          user_width_cm: input.widthCm ?? null,
          user_height_cm: input.heightCm ?? null,
          engine_provider: engine?.provider ?? null,
          engine_model: engine?.model ?? null,
        })
        .select("id")
        .single();
      displayId = (inserted.data as { id?: string } | null)?.id;
    } catch {
      /* best-effort */
    }
  }
  return { displayId, spec, engine, warnings };
}

```
