import Anthropic from "@anthropic-ai/sdk";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";
import type { z } from "zod/v4";

import { cuisineCues } from "./cuisine";
import { describeFormat, JUDGE_SYSTEM_PROMPT, REPAIR_SYSTEM_PROMPT, SYSTEM_PROMPT } from "./knowledge";
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

export type Provider = {
  id: ProviderId;
  label: string;
  model: string;
  available: () => boolean;
  generate: (request: MenuRequest, signal: AbortSignal) => Promise<GeneratedMenuCore>;
  judge: (input: JudgeInput, signal: AbortSignal) => Promise<JudgeOutput>;
  repair: (input: RepairInput, signal: AbortSignal) => Promise<GeneratedMenuCore>;
};

const LOVABLE_GATEWAY = "https://ai.gateway.lovable.dev/v1/responses";
const OPENAI_RESPONSES = "https://api.openai.com/v1/responses";

const MODELS = {
  lovable: process.env["LOVABLE_TEXT_MODEL"] ?? "openai/gpt-6-astra",
  openai: process.env["OPENAI_MODEL"] ?? "gpt-6-astra",
  claude: process.env["ANTHROPIC_MODEL"] ?? "claude-opus-5-5",
} as const;

// ---------- shared prompt assembly ----------

export function buildUserMessage(request: MenuRequest) {
  return [
    `BRIEF:\n${request.brief}`,
    `MODE: ${request.mode.toUpperCase()}`,
    `TYPE STYLE: ${request.style}`,
    `PAGE COUNT: ${request.pages}`,
    `FORMAT: ${request.size} — ${describeFormat(request.size)}`,
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
  if (!response.ok) throw new Error(await responsesError(response, `Engine request failed (${response.status}).`));

  if (!call.stream) {
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
  const base = () => {
    const headers: Record<string, string> = isLovable ? { "Lovable-API-Key": key() ?? "" } : { Authorization: `Bearer ${key() ?? ""}` };
    return { url: isLovable ? LOVABLE_GATEWAY : OPENAI_RESPONSES, headers, model: MODELS[id], stream: isLovable };
  };
  return {
    id,
    label: isLovable ? "Halftone (built-in)" : "ChatGPT",
    model: MODELS[id],
    available: () => Boolean(key()),
    async generate(request, signal) {
      const text = await callResponsesApi({
        ...base(),
        instructions: SYSTEM_PROMPT,
        text: buildUserMessage(request),
        image: request.referenceImage,
        schemaName: "menu_design",
        schema: MENU_JSON_SCHEMA,
        signal,
      });
      return parseMenuJson(text);
    },
    async judge(input, signal) {
      const text = await callResponsesApi({
        ...base(),
        instructions: JUDGE_SYSTEM_PROMPT,
        text: buildJudgeMessage(input),
        image: input.request.referenceImage,
        schemaName: "menu_judgement",
        schema: JUDGE_JSON_SCHEMA,
        signal,
      });
      return parseJudgeJson(text);
    },
    async repair(input, signal) {
      const text = await callResponsesApi({
        ...base(),
        instructions: REPAIR_SYSTEM_PROMPT,
        text: buildRepairMessage(input),
        image: input.request.referenceImage,
        schemaName: "menu_design",
        schema: MENU_JSON_SCHEMA,
        signal,
      });
      return parseMenuJson(text);
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
  async generate(request, signal) {
    const response = await anthropic().messages.parse(
      {
        model: MODELS.claude,
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
  },
  async judge(input, signal) {
    const response = await anthropic().messages.parse(
      {
        model: MODELS.claude,
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
  },
  async repair(input, signal) {
    const response = await anthropic().messages.parse(
      {
        model: MODELS.claude,
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
  },
};

export const PROVIDERS: Provider[] = [responsesProvider("lovable"), responsesProvider("openai"), claudeProvider];

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
