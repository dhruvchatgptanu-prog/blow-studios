import { z } from "zod/v4";

import type { DisplaySpec } from "./display";

/**
 * Shared menu contract used by every engine (Lovable gateway, OpenAI, Claude)
 * and by the judge. Keep this file free of server-only imports so the UI can
 * import the types.
 *
 * Two flavours of the same shape live here on purpose:
 *  - `menuWireSchema`      what we ask the models to emit (no min/max constraints,
 *                          because strict JSON-schema modes reject them)
 *  - `generatedMenuSchema` what we accept after a response comes back
 */

export const photoBriefSchema = z.object({ label: z.string(), prompt: z.string() });

export const menuItemSchema = z.object({
  name: z.string(),
  description: z.string(),
  price: z.string(),
  badge: z.string(),
  photoPrompt: z.string(),
});

export const menuSectionSchema = z.object({
  title: z.string(),
  note: z.string().nullable(),
  items: z.array(menuItemSchema),
});

export const menuWireSchema = z.object({
  mode: z.enum(["menu", "poster"]),
  restaurantName: z.string(),
  tagline: z.string(),
  eyebrow: z.string(),
  sections: z.array(menuSectionSchema),
  footerLeft: z.string(),
  footerRight: z.string(),
  followed: z.array(z.string()),
  features: z.array(z.string()),
  heroPhoto: photoBriefSchema,
  dishPhotos: z.array(photoBriefSchema),
});

export const generatedMenuSchema = menuWireSchema.extend({
  restaurantName: z.string().min(1),
  sections: z.array(menuSectionSchema).min(1),
});

/** Judge output: the final merged menu plus an audit trail. */
export const judgeWireSchema = z.object({
  menu: menuWireSchema,
  winner: z.string(),
  rationale: z.string(),
  mergedFrom: z.array(z.string()),
});

export type MenuItem = z.infer<typeof menuItemSchema>;
export type MenuSection = z.infer<typeof menuSectionSchema>;
export type PhotoBrief = z.infer<typeof photoBriefSchema>;
export type GeneratedMenuCore = z.infer<typeof generatedMenuSchema>;

export type ProviderId = "lovable" | "openai" | "claude";

export type EngineCandidate = {
  provider: ProviderId;
  model: string;
  status: "ok" | "failed";
  score: number;
  issues: string[];
  error?: string;
  ms: number;
};

export type EngineReport = {
  strategy: "single" | "best-candidate" | "judged-merge";
  candidates: EngineCandidate[];
  judge?: { provider: ProviderId; model: string; winner: string; rationale: string; mergedFrom: string[]; ms: number };
  /** One targeted proofreading pass, run only when the verifier still lists issues. */
  repair?: { provider: ProviderId; model: string; before: number; after: number; applied: boolean; ms: number };
  /** Deterministic cuisine detection used to focus plating cues and photo enrichment. */
  cuisine: string[];
  /** Learning memory: how many approved past designs were shown as examples, and which judge the data currently prefers. */
  memory: { exemplars: number; totalRecorded: number; judgeRanking: ProviderId[] };
  /** Deterministic fit to the measured display: what had to be left out or shortened. */
  fit?: { format: string; omitted: string[]; trimmedDescriptions: number };
  template?: string;
  finalScore: number;
  finalIssues: string[];
  totalMs: number;
};

/** What the server returns to the studio. `engine` is informational only. */
export type GeneratedMenu = GeneratedMenuCore & { engine?: EngineReport };

export type StyleKey = "editorial" | "modern" | "rustic" | "bold-deals";
export type OutputMode = "menu" | "poster";
export type OutputSize = "letter" | "a5" | "poster" | "wide" | "fascia-447" | "fascia-180" | "square";

export type MenuRequest = {
  brief: string;
  style: StyleKey;
  mode: OutputMode;
  pages: "1" | "2";
  size: OutputSize;
  previous?: string | undefined;
  /** data:image/...;base64,... */
  referenceImage?: string | undefined;
  /** Set by the ensemble, never by the client: detected cuisine keys. */
  cuisine?: string[] | undefined;
  /** Set by the ensemble, never by the client: approved past outputs shown as examples. */
  exemplars?: string | undefined;
  /** Chosen template id from the store wizard (see templates.ts). */
  template?: string | undefined;
  /** Measured display the design must fit (see display.ts). */
  display?: DisplaySpec | undefined;
  /** Condensed store profile from the store wizard. */
  store?: { storeType: string; priceTier: string; cuisine: string; venueName: string; audience: string; voice: string; signatureItems?: string[] | undefined } | undefined;
  storeId?: string | undefined;
  displayId?: string | undefined;
};

/**
 * Hand-written JSON schema (strict mode friendly) used by the OpenAI-compatible
 * engines. Must stay structurally identical to `menuWireSchema`.
 */
export const MENU_JSON_SCHEMA = {
  type: "object",
  properties: {
    mode: { type: "string", enum: ["menu", "poster"] },
    restaurantName: { type: "string" },
    tagline: { type: "string" },
    eyebrow: { type: "string" },
    sections: {
      type: "array",
      items: {
        type: "object",
        properties: {
          title: { type: "string" },
          note: { type: ["string", "null"] },
          items: {
            type: "array",
            items: {
              type: "object",
              properties: {
                name: { type: "string" },
                description: { type: "string" },
                price: { type: "string" },
                badge: { type: "string" },
                photoPrompt: { type: "string" },
              },
              required: ["name", "description", "price", "badge", "photoPrompt"],
              additionalProperties: false,
            },
          },
        },
        required: ["title", "note", "items"],
        additionalProperties: false,
      },
    },
    footerLeft: { type: "string" },
    footerRight: { type: "string" },
    followed: { type: "array", items: { type: "string" } },
    features: { type: "array", items: { type: "string" } },
    heroPhoto: {
      type: "object",
      properties: { label: { type: "string" }, prompt: { type: "string" } },
      required: ["label", "prompt"],
      additionalProperties: false,
    },
    dishPhotos: {
      type: "array",
      items: {
        type: "object",
        properties: { label: { type: "string" }, prompt: { type: "string" } },
        required: ["label", "prompt"],
        additionalProperties: false,
      },
    },
  },
  required: ["mode", "restaurantName", "tagline", "eyebrow", "sections", "footerLeft", "footerRight", "followed", "features", "heroPhoto", "dishPhotos"],
  additionalProperties: false,
} as const;

export const JUDGE_JSON_SCHEMA = {
  type: "object",
  properties: {
    menu: MENU_JSON_SCHEMA,
    winner: { type: "string" },
    rationale: { type: "string" },
    mergedFrom: { type: "array", items: { type: "string" } },
  },
  required: ["menu", "winner", "rationale", "mergedFrom"],
  additionalProperties: false,
} as const;

/** Strip the engine report before feeding a menu back as "previous" context. */
export function stripEngine(menu: GeneratedMenu): GeneratedMenuCore {
  const { engine: _engine, ...core } = menu;
  return core;
}
