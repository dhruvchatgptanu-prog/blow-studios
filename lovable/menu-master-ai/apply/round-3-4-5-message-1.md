Rounds 3-5 upgrade, MESSAGE 1 of 3 (pure libraries). The build will fail until message 3 is applied; that is expected — just write the files, do not try to fix the build.

Apply EXACTLY as given; overwrite/create each file verbatim; do not refactor, rename or "improve"; keep your explanation to a few lines. Do NOT touch src/lib/image-gateway.server.ts, src/routes/api/generate-image.ts, src/routes/api/edit-image.ts or src/lib/streamImage.ts. The Supabase tables public.halftone_stores, public.halftone_displays and the new columns on public.halftone_generations ALREADY EXIST (RLS enabled) — do not create, alter or migrate them.


===== FILE: src/lib/ai/schema.ts =====
```tsx
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
  store?: { storeType: string; priceTier: string; cuisine: string; venueName: string; audience: string; voice: string } | undefined;
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

```

===== FILE: src/lib/ai/cuisine.ts =====
```tsx
import { CUISINE_PLATING_CUES } from "./knowledge";
import type { GeneratedMenuCore } from "./schema";

/**
 * Deterministic cuisine detection and photo-prompt enrichment.
 *
 * Both run without any model call. Detection narrows the plating cues the
 * engines are told to apply; enrichment post-processes every photo prompt so
 * it always carries the six grammar parts (subject, plating, surface, angle,
 * light, finish) plus the realism cues research keeps pointing at: deliberate
 * imperfection, no fake steam, no non-food props. The image pipeline and its
 * PHOTO_DIRECTION suffix stay untouched; this only improves what we ask for.
 */

export type CuisineKey = keyof typeof CUISINE_PLATING_CUES;

const KEYWORDS: Record<string, string[]> = {
  "greek / yiros / gyros / souvlaki": ["yiros", "gyros", "gyro", "souvlaki", "greek", "tzatziki", "halloumi", "pita", "moussaka"],
  pizza: ["pizza", "pizzas", "pizzeria", "margherita", "pepperoni", "calzone", "crust", "slice"],
  burgers: ["burger", "burgers", "cheeseburger", "smash", "patty", "brioche"],
  "fish & chips / seafood": ["fish", "chips", "calamari", "prawn", "prawns", "seafood", "oyster", "oysters", "barramundi", "flake", "tartare", "scallop"],
  "indian / curry": ["curry", "indian", "tandoori", "naan", "biryani", "masala", "tikka", "dal", "paneer", "samosa"],
  "chinese / asian": ["chinese", "dumpling", "dumplings", "wok", "fried rice", "noodle", "noodles", "dim sum", "bao", "pho", "thai", "pad thai", "laksa", "korean", "bibimbap"],
  "japanese / sushi": ["sushi", "sashimi", "ramen", "japanese", "nigiri", "teriyaki", "katsu", "udon", "bento", "poke"],
  mexican: ["taco", "tacos", "burrito", "burritos", "mexican", "quesadilla", "nachos", "salsa", "guacamole", "enchilada"],
  "cafe / breakfast / brunch": ["cafe", "café", "brunch", "breakfast", "coffee", "latte", "avocado", "eggs", "pancakes", "toast", "granola", "croissant"],
  "bakery / dessert": ["bakery", "dessert", "desserts", "cake", "cakes", "pastry", "pastries", "donut", "doughnut", "cookie", "brownie", "gelato", "ice cream", "cheesecake", "baklava"],
  "steak / grill": ["steak", "steaks", "ribeye", "sirloin", "grill", "grilled", "bbq", "barbecue", "ribs", "brisket", "charcoal"],
  "fried chicken / wings": ["fried chicken", "wings", "chicken wings", "tenders", "nuggets", "crispy chicken", "korean fried"],
  "kebab / middle eastern": ["kebab", "kebabs", "shawarma", "falafel", "hummus", "lebanese", "turkish", "doner", "döner", "manoush", "halal snack pack", "hsp"],
  "italian / pasta": ["pasta", "spaghetti", "lasagne", "lasagna", "risotto", "gnocchi", "carbonara", "bolognese", "italian", "ravioli", "penne"],
  "salads / healthy": ["salad", "salads", "bowl", "bowls", "healthy", "vegan", "plant-based", "wrap", "wraps", "smoothie bowl", "acai"],
  "drinks / bubble tea / smoothies": ["bubble tea", "boba", "smoothie", "smoothies", "juice", "juices", "milkshake", "shakes", "iced", "frappe", "mocktail", "cocktail"],
};

/** Generic ingredient words that appear across cuisines count less than cuisine-defining words. */
const GENERIC_WORDS = new Set(["chips", "fish", "chicken", "eggs", "toast", "salad", "salads", "bowl", "bowls", "wrap", "wraps", "grill", "grilled", "slice", "coffee", "iced", "juice", "juices", "bbq", "crust", "prawn", "prawns", "healthy"]);

/** Returns matching cuisine keys, strongest first. Empty when nothing matched. */
export function detectCuisine(brief: string): string[] {
  const lower = ` ${brief.toLowerCase().replace(/[^a-z0-9äöüéèêàçñ&'\s-]/g, " ").replace(/\s+/g, " ")} `;
  const intro = lower.slice(0, 90);
  const scored = Object.entries(KEYWORDS)
    .map(([key, words]) => {
      let score = 0;
      for (const word of words) {
        const weight = GENERIC_WORDS.has(word) ? 1 : 3;
        const occurrences = lower.split(` ${word} `).length - 1 + (lower.split(` ${word}s `).length - 1);
        if (occurrences === 0) continue;
        score += weight * Math.min(occurrences, 4);
        if (intro.includes(` ${word}`)) score += weight * 2;
      }
      return { key, score };
    })
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, 2).map((entry) => entry.key);
}

export function cuisineCues(cuisines: string[]) {
  return cuisines.map((key) => `${key}: ${CUISINE_PLATING_CUES[key] ?? ""}`).filter((line) => !line.endsWith(": "));
}

type PhotoDefaults = { surface: string; angle: string; light: string };

const DEFAULTS: Record<string, PhotoDefaults> = {
  "greek / yiros / gyros / souvlaki": { surface: "on a dark round timber board over a neutral slate-grey surface", angle: "45-degree angle with a 50 mm full-frame look", light: "one large softbox key from upper left, soft white-card fill" },
  pizza: { surface: "on a dark steel tray over a floured timber peel", angle: "overhead for the whole pie, or a low 30-degree angle for a close-up", light: "large softbox from the back-left so the cheese glistens, soft fill from the front" },
  burgers: { surface: "on brown kraft paper over a dark wooden table", angle: "eye-level three-quarter view with an 85 mm look", light: "45-degree side light with white-card fill and a soft rim highlight on the bun" },
  "fish & chips / seafood": { surface: "on a paper-lined stainless tray over a pale timber counter", angle: "45-degree angle, 50 mm look", light: "bright softbox key from upper left, gentle fill, crisp specular highlights on the batter" },
  "indian / curry": { surface: "in copper and brass serveware on a dark slate surface", angle: "overhead with a slight tilt, 50 mm look", light: "warm softbox from the back-right, soft front fill, glossy sauce highlights" },
  "chinese / asian": { surface: "on white porcelain over a dark lacquered table", angle: "45-degree angle, 50 mm look", light: "soft backlight to catch the sauce gloss, white-card fill from the front" },
  "japanese / sushi": { surface: "on a matte black slate over a light ash table", angle: "overhead or a low 20-degree angle for nigiri rows, 85 mm look", light: "soft diffused key from the side, subtle fill, no harsh specular" },
  mexican: { surface: "on a weathered wooden board over a terracotta-toned surface", angle: "45-degree angle, 50 mm look", light: "warm softbox key from upper left, soft fill" },
  "cafe / breakfast / brunch": { surface: "on white ceramic over a light oak café table", angle: "overhead or 45-degree, 50 mm look", light: "soft window-style daylight from the side, white-card fill" },
  "bakery / dessert": { surface: "on white marble with a light flour dusting", angle: "45-degree angle with an 85 mm macro look for texture", light: "soft diffused key from the back-left, gentle front fill" },
  "steak / grill": { surface: "on a dark cast-iron skillet or charred timber board over slate", angle: "45-degree angle, 50 mm look", light: "hard-edged softbox from the back-left for crust texture, low fill for drama" },
  "fried chicken / wings": { surface: "in a kraft tray over a dark wooden counter", angle: "45-degree angle, 50 mm look", light: "bright softbox key from upper left, crisp highlights on the crust" },
  "kebab / middle eastern": { surface: "on a stainless tray lined with flatbread over a dark surface", angle: "45-degree angle, 50 mm look", light: "warm softbox key from the back-left, soft fill" },
  "italian / pasta": { surface: "in a shallow wide ceramic bowl on a linen-covered table", angle: "45-degree angle, 50 mm look", light: "soft daylight-style key from the side, gentle fill" },
  "salads / healthy": { surface: "in a wide stoneware bowl on a light timber table", angle: "overhead, 50 mm look", light: "bright soft daylight-style key, airy fill" },
  "drinks / bubble tea / smoothies": { surface: "on a clean light counter with a soft neutral backdrop", angle: "eye-level, 85 mm look", light: "controlled backlight glowing through the drink, soft front fill" },
};

const GENERIC: PhotoDefaults = { surface: "on a dark round timber board over a neutral surface", angle: "45-degree angle with a 50 mm full-frame look", light: "one large softbox key from upper left with soft white-card fill" };

const REALISM = "Natural imperfections: slightly uneven arrangement, natural colour variation, a small sauce or crumb trace on the rim. No fake steam, no non-food props, no text, no logo, no watermark, no illustration, no CGI, no plastic-looking food.";

function has(prompt: string, re: RegExp) {
  return re.test(prompt);
}

/** Ensures a prompt carries surface, angle, light, finish and realism cues without duplicating what the model already wrote. Idempotent. */
export function enrichPhotoPrompt(prompt: string, cuisines: string[], shape: "square" | "wide" = "square"): string {
  const defaults = DEFAULTS[cuisines[0] ?? ""] ?? GENERIC;
  const parts = [prompt.trim().replace(/\s+/g, " ")];
  if (!has(prompt, /\b(board|plate|platter|tray|marble|paper|table|counter|slate|skillet|bowl|surface|peel|linen)\b/i)) parts.push(`Served ${defaults.surface}.`);
  if (!has(prompt, /\b(overhead|top-down|45|eye-level|three-quarter|angle|macro|mm)\b/i)) parts.push(`Shot from a ${defaults.angle}.`);
  if (!has(prompt, /\b(softbox|light|lighting|lit|backlight|daylight)\b/i)) parts.push(`Lit by ${defaults.light}.`);
  if (!has(prompt, /\b(sharp|focus|depth of field)\b/i)) parts.push("Sharp focus across the whole dish with a softly blurred background.");
  if (!has(prompt, /\b(imperfect|uneven|variation|natural)\b/i) || !has(prompt, /\bno (fake )?steam\b/i)) parts.push(REALISM);
  if (!has(prompt, /\bcomposition\b/i)) parts.push(shape === "wide" ? "Wide banner composition, complete scene visible, clean edges." : "Square composition, complete dish fully visible, clean edges for cropping.");
  return parts.join(" ");
}

/** Enriches every photo prompt in a menu. Returns a new object; never mutates. */
export function enrichMenuPhotos(menu: GeneratedMenuCore, cuisines: string[]): GeneratedMenuCore {
  return {
    ...menu,
    heroPhoto: { ...menu.heroPhoto, prompt: enrichPhotoPrompt(menu.heroPhoto.prompt, cuisines, "wide") },
    dishPhotos: menu.dishPhotos.map((photo) => ({ ...photo, prompt: enrichPhotoPrompt(photo.prompt, cuisines) })),
    sections: menu.sections.map((section) => ({
      ...section,
      items: section.items.map((item) => ({ ...item, photoPrompt: enrichPhotoPrompt(item.photoPrompt, cuisines) })),
    })),
  };
}

```

===== FILE: src/lib/ai/verify.ts =====
```tsx
import type { GeneratedMenuCore, MenuRequest } from "./schema";

/**
 * Deterministic brief-compliance verifier. This is Halftone's "own AI" layer:
 * no model involved, so it cannot hallucinate. It scores a candidate 0-100 and
 * lists concrete issues that the judge is told to fix.
 */

export type Verification = { score: number; issues: string[]; checks: Record<string, number> };

const PRICE_RE = /(?:[$€£¥]\s?\d{1,5}(?:[.,]\d{1,2})?|\b\d{1,5}(?:[.,]\d{1,2})?\s?(?:€|£|kr|chf|aud|usd|nzd|cad|gbp|eur|rs|₹|aed|r)\b)/gi;
const FORBIDDEN_PHOTO_WORDS = ["text", "logo", "logos", "watermark", "price", "prices", "signage", "labels?", "menu board", "illustration", "illustrated", "cartoon", "render", "rendered", "cgi", "3d", "plastic", "typography", "lettering", "caption", "writing"];
const COUNT_WORDS: Record<string, number> = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, twelve: 12 };

function normalisePrice(raw: string) {
  const digits = raw.replace(/[^\d.,]/g, "").replace(",", ".");
  const value = Number.parseFloat(digits);
  return Number.isFinite(value) ? value.toFixed(2) : digits;
}

export function extractBriefPrices(brief: string) {
  return Array.from(new Set((brief.match(PRICE_RE) ?? []).map(normalisePrice)));
}

/** Dish names: segments that sit directly before a dash/colon/price, e.g. "Yiros Meal Deal — 3 lamb yiros ... $49.90". */
export function extractBriefNames(brief: string) {
  const names = new Set<string>();
  const cleaned = brief.replace(/\b(include|includes|including|offers?|deals?|items?)\s*:\s*/gi, ";");
  const NAME_RE = /^([A-Z][\w'&]*(?:\s+(?:[A-Z][\w'&]*|of|and|&|the|de|la|al)){0,5})\s*(?:—|–|-|:|\(|\d+["”]|[$€£])/;
  // Segments split on list separators and sentence boundaries; commas only count when the segment ends in a price.
  const segments = cleaned.split(/[;\n•]|\.\s+(?=[A-Z])/).flatMap((part) => {
    const commaParts = part.split(/,\s+(?=[A-Z])/);
    return commaParts.length > 1 && commaParts.every((piece) => /[$€£]\s?\d|\d\s?(?:€|£)/.test(piece)) ? commaParts : [part];
  });
  for (const part of segments) {
    const segment = part.trim().replace(/^(?:and|&|plus)\s+/i, "");
    const match = NAME_RE.exec(segment);
    if (match?.[1] && match[1].length >= 4) names.add(match[1].trim());
  }
  const quoted = brief.match(/["“”]([^"“”]{3,40})["“”]/g) ?? [];
  for (const q of quoted) {
    const inner = q.replace(/^["“”]|["“”]$/g, "").trim();
    if (/^[A-Za-z]/.test(inner) && !/[;$€£]/.test(inner)) names.add(inner);
  }
  return Array.from(names).filter((name) => !/^(create|make|design|use|include|build|the|a|an|our|my|please|keep|add|redesign|poster|menu|sign|board|sections?|prices?|format|style|mode|note|notes)\b/i.test(name));
}

function extractQuantityPhrases(brief: string) {
  const phrases: { count: number; noun: string }[] = [];
  const re = /\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+(?:x\s+)?([a-z][a-z-]{2,}(?:\s+[a-z][a-z-]{2,})?)/gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(brief)) !== null) {
    const count = /^\d+$/.test(match[1] ?? "") ? Number(match[1]) : COUNT_WORDS[(match[1] ?? "").toLowerCase()] ?? 0;
    const noun = (match[2] ?? "").toLowerCase();
    if (count > 0 && !/^(page|pages|section|sections|item|items|offer|offers|deal|deals|column|columns|inch|in|cm|mm)$/.test(noun.split(" ")[0] ?? "")) {
      phrases.push({ count, noun });
    }
  }
  return phrases;
}

function explicitCount(brief: string, nouns: RegExp) {
  const match = new RegExp(`\\b(\\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\\s+(?:${nouns.source})\\b`, "i").exec(brief);
  if (!match) return null;
  return /^\d+$/.test(match[1] ?? "") ? Number(match[1]) : COUNT_WORDS[(match[1] ?? "").toLowerCase()] ?? null;
}

export function verifyMenu(menu: GeneratedMenuCore, request: MenuRequest): Verification {
  const issues: string[] = [];
  const checks: { prices?: number; names?: number; quantities?: number; photos?: number; structure?: number; [key: string]: number | undefined } = {};
  const items = menu.sections.flatMap((section) => section.items);
  const brief = request.brief;

  // 1. Prices (weight 40)
  const briefPrices = extractBriefPrices(brief);
  const outputPrices = new Set(items.map((item) => normalisePrice(item.price)).filter(Boolean));
  if (briefPrices.length > 0) {
    const missing = briefPrices.filter((price) => !outputPrices.has(price));
    checks.prices = Math.round(((briefPrices.length - missing.length) / briefPrices.length) * 40);
    if (missing.length) issues.push(`Prices from the brief are missing or altered: ${missing.join(", ")}.`);
    const extra = Array.from(outputPrices).filter((price) => !briefPrices.includes(price));
    if (extra.length && !request.previous) {
      checks.prices = Math.max(0, (checks.prices ?? 0) - Math.min(10, extra.length * 3));
      issues.push(`Prices not present in the brief were invented: ${extra.join(", ")}.`);
    }
  } else {
    checks.prices = items.every((item) => item.price.trim().length > 0) ? 40 : 25;
    if ((checks.prices ?? 0) < 40) issues.push("Some items have no price.");
  }

  // 2. Names (weight 20)
  const briefNames = extractBriefNames(brief);
  const haystack = items.map((item) => item.name.toLowerCase()).join(" | ");
  if (briefNames.length > 0) {
    const missing = briefNames.filter((name) => !haystack.includes(name.toLowerCase()));
    checks.names = Math.round(((briefNames.length - missing.length) / briefNames.length) * 20);
    if (missing.length) issues.push(`Dish names from the brief are missing or respelled: ${missing.join(", ")}.`);
  } else {
    checks.names = 20;
  }

  // 3. Quantities / inclusions (weight 10)
  const phrases = extractQuantityPhrases(brief);
  if (phrases.length > 0) {
    const body = items.map((item) => `${item.name} ${item.description}`.toLowerCase()).join(" | ");
    const missing = phrases.filter((phrase) => {
      const noun = phrase.noun.split(" ")[0] ?? "";
      const stem = noun.replace(/(es|s)$/, "");
      const word = Object.keys(COUNT_WORDS).find((key) => COUNT_WORDS[key] === phrase.count) ?? "";
      const pattern = new RegExp(`\\b(?:${phrase.count}|${word})\\s+(?:[a-z-]+\\s+){0,2}${stem}`, "i");
      return !pattern.test(body);
    });
    checks.quantities = Math.round(((phrases.length - missing.length) / phrases.length) * 10);
    if (missing.length) issues.push(`Deal inclusions are not restated with their quantity: ${missing.map((phrase) => `${phrase.count} ${phrase.noun}`).join(", ")}.`);
  } else {
    checks.quantities = 10;
  }

  // 4. Photo prompt hygiene (weight 15)
  const prompts = [menu.heroPhoto.prompt, ...menu.dishPhotos.map((photo) => photo.prompt), ...items.map((item) => item.photoPrompt)];
  let photoPenalty = 0;
  for (const prompt of prompts) {
    const lower = prompt.toLowerCase();
    const hit = FORBIDDEN_PHOTO_WORDS.find((word) => {
      const re = new RegExp(`\\b${word.trim()}\\b`, "i");
      if (!re.test(lower)) return false;
      const negated = new RegExp(`\\b(?:no|without|never|not)\\b[^.;]{0,90}\\b${word.trim()}\\b`, "i");
      return !negated.test(lower);
    });
    if (hit) photoPenalty += 3;
    if (prompt.split(/\s+/).length < 18) photoPenalty += 2;
  }
  checks.photos = Math.max(0, 15 - photoPenalty);
  if (photoPenalty > 0) issues.push("Some photo prompts are too short or mention text/logo/price/illustration words; rewrite them with the photo prompt grammar.");

  // 5. Structure (weight 15)
  let structure = 15;
  if (!menu.restaurantName.trim()) { structure -= 5; issues.push("Restaurant name is empty."); }
  if (menu.sections.length === 0 || items.length === 0) { structure -= 10; issues.push("No sections or items."); }
  if (menu.mode !== request.mode) { structure -= 5; issues.push(`Mode should be ${request.mode}.`); }
  const offerCount = explicitCount(brief, /offers?|deals?|items?|dishes|packs?|combos?/);
  if (offerCount && request.mode === "poster" && items.length !== offerCount) { structure -= 5; issues.push(`Brief asks for ${offerCount} offers but ${items.length} were produced.`); }
  if (request.mode === "poster" && !offerCount && (items.length < 2 || items.length > 4) && briefNames.length === 0) { structure -= 3; issues.push("Posters work best with 2-4 offers unless the brief says otherwise."); }
  if (request.mode === "menu" && request.pages === "1" && items.length > 18) { structure -= 3; issues.push("Too many items for one page."); }
  if (request.mode === "menu" && menu.sections.length < 2 && items.length > 6) { structure -= 2; issues.push("Group items into sections."); }
  if (/\d{2,}[\s-]?\d{3,}/.test(`${menu.footerLeft} ${menu.footerRight}`) && !/\d{2,}[\s-]?\d{3,}/.test(brief)) { structure -= 5; issues.push("A phone number appears that the brief did not supply."); }
  if (menu.followed.length > 6) { structure -= 1; }
  if (menu.features.length > 0 && !/fresh|made|house|halal|vegan|gluten|organic|local|award|family|since|open|deliver|pickup|order/i.test(brief)) { structure -= 2; issues.push("'features' contains claims the brief does not support."); }
  for (const item of items) {
    if (/[!]{1,}/.test(item.description)) { structure -= 1; issues.push(`Remove exclamation marks from "${item.name}".`); break; }
  }
  if (request.display) {
    const budget = request.display.budget;
    const limit = request.mode === "poster" ? budget.maxOffers : budget.maxItems;
    if (items.length > limit) { structure -= 3; issues.push(`The display fits ${limit} ${request.mode === "poster" ? "offers" : "items"} legibly; ${items.length} were produced. Keep the most important ones.`); }
    const longNames = items.filter((item) => item.name.trim().split(/\s+/).length > budget.maxNameWords + 1);
    if (longNames.length) { structure -= 1; issues.push(`Names longer than ${budget.maxNameWords} words will not read from ${request.display.viewingDistanceM} m: ${longNames.slice(0, 3).map((item) => item.name).join(", ")}.`); }
    const longDescriptions = items.filter((item) => item.description.trim().split(/\s+/).filter(Boolean).length > budget.maxDescriptionWords + 2);
    if (longDescriptions.length) { structure -= 1; issues.push(`Descriptions longer than ${budget.maxDescriptionWords} words exceed the display budget on ${longDescriptions.length} item(s).`); }
  }
  checks.structure = Math.max(0, structure);

  const score = Math.max(0, Math.min(100, Object.values(checks).reduce<number>((sum, value) => sum + (value ?? 0), 0)));
  return { score, issues: Array.from(new Set(issues)), checks: checks as Record<string, number> };
}

```

===== FILE: src/lib/ai/coach.ts =====
```tsx
import { extractBriefNames, extractBriefPrices } from "./verify";

/**
 * Brief coach: deterministic, instant hints shown under the brief before any
 * AI runs. Better briefs are the single biggest lever on output quality, and
 * these checks mirror what the verifier will later score the engines on.
 */

export type CoachHint = { level: "fix" | "tip"; text: string };

export type CoachReport = { score: number; hints: CoachHint[] };

export function coachBrief(brief: string, mode: "menu" | "poster"): CoachReport {
  const text = brief.trim();
  const hints: CoachHint[] = [];
  if (text.length < 20) return { score: 0, hints: [{ level: "fix", text: "Describe the venue, the dishes or offers, and the exact prices." }] };

  const prices = extractBriefPrices(text);
  const names = extractBriefNames(text);
  const hasVenue = /\b(for|at|called|named)\s+[A-Z][\w'&]+(?:\s+[A-Z][\w'&]+)*/.test(text) || /^[A-Z][\w'&]+(?:\s+[A-Z][\w'&]+)*\s*[:—-]/.test(text);
  const hasCount = /\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+(offers?|deals?|items?|dishes|sections?)\b/i.test(text);
  const hasPhone = /\d{2,}[\s-]?\d{3,}[\s-]?\d{3,}/.test(text);
  const hasNegatives = /\b(no|without|don't|do not|never)\b/i.test(text);
  const mentionsCurrencyWord = /\b(dollars?|euros?|pounds?|aud|usd|gbp|eur|nzd|cad)\b/i.test(text);

  let score = 100;
  if (!hasVenue) { score -= 20; hints.push({ level: "fix", text: "Name the venue (e.g. 'for Yiros House') so the brand zone is exact." }); }
  if (prices.length === 0) {
    if (mode === "poster") { score -= 35; hints.push({ level: "fix", text: "Give every offer its exact price (e.g. $49.90). Posters without prices get invented ones." }); }
    else if (!mentionsCurrencyWord) { score -= 15; hints.push({ level: "tip", text: "Add prices, or say which currency to use, so none are invented." }); }
  }
  if (mode === "poster" && names.length === 0 && prices.length > 0) { score -= 15; hints.push({ level: "tip", text: "Write each offer as 'Name — what's included $price' so names stay exact." }); }
  if (mode === "poster" && prices.length > 0 && names.length > 0 && names.length !== prices.length) { score -= 5; hints.push({ level: "tip", text: `Found ${names.length} named offers but ${prices.length} prices; check each offer has one price.` }); }
  if (mode === "poster" && !hasCount && names.length === 0) { score -= 5; hints.push({ level: "tip", text: "Say how many offers you want (2-4 works best)." }); }
  if (mode === "menu" && !/\b(section|sections|starters?|mains?|sides?|desserts?|drinks?|breakfast|lunch|dinner)\b/i.test(text)) { score -= 10; hints.push({ level: "tip", text: "List the sections you want (e.g. starters, mains, sides) in the order they should appear." }); }
  if (mode === "poster" && /\b(\d{1,2})\s+[a-z]/i.test(text) === false && prices.length > 0) { score -= 5; hints.push({ level: "tip", text: "Spell out what each deal includes with quantities (e.g. '3 lamb yiros and large chips')." }); }
  if (!hasPhone) hints.push({ level: "tip", text: "No phone number given, so none will be printed. Add one if you want it on the design." });
  if (!hasNegatives && text.length < 160) hints.push({ level: "tip", text: "Mention anything to avoid (e.g. 'no descriptions', 'no red') and any colours or fonts you want." });

  return { score: Math.max(0, score), hints: hints.slice(0, 4) };
}

```

===== FILE: src/lib/ai/store.ts =====
```tsx
import { z } from "zod/v4";

/**
 * Store analysis: turns a description plus storefront / interior / display
 * photos into a structured StoreProfile. The profile drives template
 * recommendation, format choice, palette and copy voice.
 *
 * Two paths produce a profile:
 *   - `analyzeStore` (server) asks a vision-capable engine for the schema below;
 *   - `profileFromText` is the deterministic fallback used when no engine is
 *     available and as a sanity baseline merged with the model's answer.
 */

export const STORE_TYPES = [
  "takeaway",
  "fast-casual",
  "cafe",
  "casual-dining",
  "fine-dining",
  "bar-pub",
  "food-truck",
  "kiosk-stall",
  "bakery-dessert",
  "ghost-kitchen",
] as const;
export type StoreType = (typeof STORE_TYPES)[number];

export const PRICE_TIERS = ["budget", "mid", "premium", "luxury"] as const;
export const ORDER_FLOWS = ["counter", "table-service", "drive-thru", "window", "app-delivery", "mixed"] as const;
export const DISPLAY_SURFACES = ["wall-board", "digital-screen", "window", "counter-card", "a-frame", "fascia", "table-menu", "handout", "drive-thru-board"] as const;
export const LIGHTING = ["bright", "neutral", "dim", "outdoor"] as const;

export const storeProfileSchema = z.object({
  storeType: z.enum(STORE_TYPES),
  cuisine: z.string(),
  venueName: z.string(),
  priceTier: z.enum(PRICE_TIERS),
  orderFlow: z.enum(ORDER_FLOWS),
  vibe: z.array(z.string()),
  /** Dominant brand colours as hex strings, strongest first. */
  palette: z.array(z.string()),
  lighting: z.enum(LIGHTING),
  displaySurfaces: z.array(z.enum(DISPLAY_SURFACES)),
  languages: z.array(z.string()),
  audience: z.string(),
  signatureItems: z.array(z.string()),
  /** What the analysis is sure about vs. guessing. */
  confidence: z.number(),
  notes: z.string(),
});
export type StoreProfile = z.infer<typeof storeProfileSchema>;

export const STORE_PROFILE_JSON_SCHEMA = {
  type: "object",
  properties: {
    storeType: { type: "string", enum: [...STORE_TYPES] },
    cuisine: { type: "string" },
    venueName: { type: "string" },
    priceTier: { type: "string", enum: [...PRICE_TIERS] },
    orderFlow: { type: "string", enum: [...ORDER_FLOWS] },
    vibe: { type: "array", items: { type: "string" } },
    palette: { type: "array", items: { type: "string" } },
    lighting: { type: "string", enum: [...LIGHTING] },
    displaySurfaces: { type: "array", items: { type: "string", enum: [...DISPLAY_SURFACES] } },
    languages: { type: "array", items: { type: "string" } },
    audience: { type: "string" },
    signatureItems: { type: "array", items: { type: "string" } },
    confidence: { type: "number" },
    notes: { type: "string" },
  },
  required: ["storeType", "cuisine", "venueName", "priceTier", "orderFlow", "vibe", "palette", "lighting", "displaySurfaces", "languages", "audience", "signatureItems", "confidence", "notes"],
  additionalProperties: false,
} as const;

/** Design guidance per store type: what works, what to avoid, which formats, which copy voice. */
export const STORE_TYPE_GUIDE: Record<StoreType, { label: string; formats: string[]; voice: string; design: string; avoid: string; itemsPerBoard: [number, number] }> = {
  takeaway: {
    label: "Takeaway / shopfront",
    formats: ["fascia", "wall-board", "window", "counter-card"],
    voice: "Short, plain, value-led. Deals first, big prices, inclusions spelled out.",
    design: "High contrast, bold condensed type, photo-led deals, strong brand zone, prices as the loudest element after names.",
    avoid: "Long descriptions, script type, pale colours on pale backgrounds, more than 5 offers per board.",
    itemsPerBoard: [3, 12],
  },
  "fast-casual": {
    label: "Fast casual",
    formats: ["digital-screen", "wall-board", "handout"],
    voice: "Friendly, quick to scan. Build-your-own steps and combos are explicit.",
    design: "Clean grid, 2-3 clear categories per board, photo tiles for heroes, consistent price column.",
    avoid: "Dense paragraphs, more than 3 fonts, mixed photo styles.",
    itemsPerBoard: [6, 18],
  },
  cafe: {
    label: "Café / brunch",
    formats: ["wall-board", "a-frame", "table-menu", "counter-card"],
    voice: "Warm, specific ingredients, origin and method words (single-origin, sourdough, slow-roasted).",
    design: "Warm paper tones, serif or humanist sans, generous spacing, small hand-drawn accents, coffee and food separated clearly.",
    avoid: "Neon, heavy drop shadows, aggressive deal badges.",
    itemsPerBoard: [8, 24],
  },
  "casual-dining": {
    label: "Casual dining",
    formats: ["table-menu", "handout", "window"],
    voice: "Descriptive but efficient, 8-14 word descriptions, sections ordered as a meal.",
    design: "Two-column or sectioned layout, dotted leaders or aligned prices, one accent colour, a hero photo at most.",
    avoid: "Prices in a long vertical column that invites comparison, clutter, badges on every item.",
    itemsPerBoard: [16, 40],
  },
  "fine-dining": {
    label: "Fine dining",
    formats: ["table-menu", "handout"],
    voice: "Minimal, precise, ingredient-led, no adjectives like delicious; courses named, provenance where true.",
    design: "Abundant whitespace, refined serif, no photos or one restrained image, prices without currency symbols if the brief allows, muted palette.",
    avoid: "Deal language, badges, bright colours, many fonts.",
    itemsPerBoard: [6, 18],
  },
  "bar-pub": {
    label: "Bar / pub",
    formats: ["wall-board", "table-menu", "a-frame", "digital-screen"],
    voice: "Punchy, a little playful, drinks and food separated, specials time-boxed only if true.",
    design: "Dark backgrounds with light type work well, chalk or slab display faces, clear drink size/price grids.",
    avoid: "Low contrast in dim light, tiny type, photos of drinks that look fake.",
    itemsPerBoard: [8, 24],
  },
  "food-truck": {
    label: "Food truck",
    formats: ["fascia", "wall-board", "a-frame"],
    voice: "Very short names, one signature item as hero, combos explicit.",
    design: "Read from 3-6 m: huge item names, prices beside them, 1 hero photo, strong colour blocking.",
    avoid: "More than 8 items, descriptions longer than 6 words, thin type.",
    itemsPerBoard: [3, 8],
  },
  "kiosk-stall": {
    label: "Kiosk / market stall",
    formats: ["counter-card", "a-frame", "wall-board"],
    voice: "Direct and friendly; what it is, what it costs.",
    design: "Compact board, 2-4 columns, prices aligned, one accent colour, clear brand mark.",
    avoid: "Small photos, paragraphs, light type on busy backgrounds.",
    itemsPerBoard: [4, 12],
  },
  "bakery-dessert": {
    label: "Bakery / dessert",
    formats: ["counter-card", "wall-board", "window", "handout"],
    voice: "Sensory texture words (flaky, glossy, crumb) kept honest; sizes and portions explicit.",
    design: "Soft palette or warm neutrals, macro photography, product names large, prices small but clear.",
    avoid: "Deal badges on pastries, harsh red, cluttered grids.",
    itemsPerBoard: [6, 20],
  },
  "ghost-kitchen": {
    label: "Delivery-only kitchen",
    formats: ["handout", "digital-screen"],
    voice: "App-ready: names searchable, inclusions explicit, allergens only if supplied.",
    design: "Square photo tiles, short names, consistent price formatting for app import.",
    avoid: "Long section notes, visual tricks that do not survive an app listing.",
    itemsPerBoard: [8, 30],
  },
};

const TYPE_KEYWORDS: Record<StoreType, string[]> = {
  takeaway: ["takeaway", "take away", "take-away", "takeout", "shopfront", "fish and chips", "fish & chips", "kebab shop", "charcoal chicken", "pizza shop", "fascia", "shop front"],
  "fast-casual": ["fast casual", "fast-casual", "counter service", "build your own", "bowl bar", "burger bar", "franchise", "quick service", "qsr"],
  cafe: ["cafe", "café", "coffee shop", "brunch", "espresso", "bakery cafe", "breakfast"],
  "casual-dining": ["restaurant", "dine in", "dine-in", "bistro", "family restaurant", "trattoria", "taverna", "grill house", "casual dining"],
  "fine-dining": ["fine dining", "degustation", "tasting menu", "chef's", "michelin", "hatted", "upscale", "high-end", "sommelier"],
  "bar-pub": ["bar", "pub", "tavern", "brewery", "taproom", "wine bar", "cocktail", "sports bar", "hotel bistro"],
  "food-truck": ["food truck", "truck", "van", "trailer", "mobile kitchen", "festival"],
  "kiosk-stall": ["kiosk", "stall", "market", "pop-up", "pop up", "cart", "booth"],
  "bakery-dessert": ["bakery", "patisserie", "pastry", "dessert bar", "gelato", "ice cream", "cake shop", "donut"],
  "ghost-kitchen": ["ghost kitchen", "dark kitchen", "delivery only", "delivery-only", "cloud kitchen", "uber eats only"],
};

const PREMIUM_WORDS = /\b(premium|upscale|fine|degustation|tasting|hatted|michelin|chef|sommelier|artisan|wagyu|truffle|oyster|champagne)\b/i;
const BUDGET_WORDS = /\b(cheap|budget|value|deal|deals|combo|family pack|\$\s?[1-9](?:\.\d\d)?\b|student)\b/i;

/** Deterministic baseline profile from text alone. Never throws. */
export function profileFromText(description: string, venueName = ""): StoreProfile {
  const lower = description.toLowerCase();
  const scored = (Object.keys(TYPE_KEYWORDS) as StoreType[])
    .map((type) => ({ type, hits: TYPE_KEYWORDS[type].reduce((count, word) => count + (lower.includes(word) ? (word.length > 6 ? 2 : 1) : 0), 0) }))
    .sort((a, b) => b.hits - a.hits);
  const storeType: StoreType = scored[0] && scored[0].hits > 0 ? scored[0].type : "casual-dining";
  const priceTier = PREMIUM_WORDS.test(description) ? (/\b(michelin|degustation|hatted|tasting)\b/i.test(description) ? "luxury" : "premium") : BUDGET_WORDS.test(description) ? "budget" : "mid";
  const orderFlow = /\bdrive[- ]?thru\b/i.test(description) ? "drive-thru" : /\b(table service|waiter|waitress|dine in|dine-in|bookings?)\b/i.test(description) ? "table-service" : /\b(window service|serving hatch|order(?:ing)? window|through the window|walk[- ]up window)\b/i.test(description) ? "window" : /\b(delivery only|uber eats|doordash|deliveroo)\b/i.test(description) ? "app-delivery" : storeType === "casual-dining" || storeType === "fine-dining" ? "table-service" : "counter";
  const surfaces = new Set<(typeof DISPLAY_SURFACES)[number]>();
  if (/\bfascia|shop ?front sign|awning\b/i.test(description)) surfaces.add("fascia");
  if (/\b(wall board|menu board|board above|behind the counter)\b/i.test(description)) surfaces.add("wall-board");
  if (/\b(tv|screen|digital|display screen)\b/i.test(description)) surfaces.add("digital-screen");
  if (/\bwindow\b/i.test(description)) surfaces.add("window");
  if (/\b(a-frame|a frame|sandwich board|chalkboard outside)\b/i.test(description)) surfaces.add("a-frame");
  if (/\b(table menu|printed menu|menus on the table)\b/i.test(description)) surfaces.add("table-menu");
  if (surfaces.size === 0) for (const surface of STORE_TYPE_GUIDE[storeType].formats.slice(0, 2)) surfaces.add(surface as (typeof DISPLAY_SURFACES)[number]);
  const vibe = ["modern", "rustic", "minimal", "retro", "family", "industrial", "bright", "cosy", "street", "coastal", "traditional", "playful", "luxury", "healthy"].filter((word) => lower.includes(word));
  const palette = Array.from(new Set((description.match(/#[0-9a-f]{6}\b/gi) ?? []).map((hex) => hex.toLowerCase())));
  const lighting = /\b(dim|moody|candle|low light)\b/i.test(description) ? "dim" : /\b(outdoor|outside|street|market)\b/i.test(description) ? "outdoor" : /\b(bright|fluorescent|daylight)\b/i.test(description) ? "bright" : "neutral";
  const languages = Array.from(new Set(["english", ...(lower.match(/\b(greek|italian|chinese|japanese|korean|vietnamese|thai|arabic|turkish|hindi|spanish|french|german|portuguese)\b/g) ?? [])]));
  return {
    storeType,
    cuisine: "",
    venueName,
    priceTier,
    orderFlow,
    vibe,
    palette,
    lighting,
    displaySurfaces: Array.from(surfaces),
    languages,
    audience: "",
    signatureItems: [],
    confidence: scored[0] && scored[0].hits > 0 ? 0.5 : 0.25,
    notes: "Baseline profile derived from the written description only.",
  };
}

/** Merge a model profile with the text baseline: the model wins on everything it filled in. */
export type PartialProfile = { [K in keyof StoreProfile]?: StoreProfile[K] | undefined };

export function mergeProfiles(baseline: StoreProfile, model: PartialProfile | undefined): StoreProfile {
  if (!model) return baseline;
  const merged: StoreProfile = { ...baseline };
  for (const key of Object.keys(model) as (keyof StoreProfile)[]) {
    const value = model[key];
    if (value === undefined || value === null) continue;
    if (typeof value === "string" && !value.trim()) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    (merged as Record<string, unknown>)[key] = value;
  }
  return storeProfileSchema.parse(merged);
}

export const STORE_ANALYSIS_PROMPT = [
  "You are a hospitality brand analyst. From the owner's description and any attached photos of the venue (storefront, interior, counter, existing signage), produce a StoreProfile JSON.",
  "Be concrete and honest: only report what the text or photos support; set confidence between 0 and 1. venueName must be the exact name as written or visible on signage; empty string if unknown.",
  "palette: 2-5 hex colours that dominate the brand (signage, walls, logo), strongest first; approximate from photos. If no photo and no colours given, return an empty array.",
  "displaySurfaces: every surface where a menu is or could be displayed that you can see or that the description mentions.",
  "signatureItems: dishes named in the description or clearly visible on existing signage; never invent.",
  `storeType must be one of: ${STORE_TYPES.join(", ")}. priceTier one of: ${PRICE_TIERS.join(", ")}. orderFlow one of: ${ORDER_FLOWS.join(", ")}. lighting one of: ${LIGHTING.join(", ")}.`,
  "notes: 2-4 sentences on what a menu for this venue must get right (reading distance, light, audience, tone).",
  "Return JSON only.",
].join("\n");

```

===== FILE: src/lib/ai/templates.ts =====
```tsx
import type { StyleKey } from "./schema";
import { STORE_TYPE_GUIDE, type StoreProfile, type StoreType } from "./store";

/**
 * Template library. A template is a complete visual direction: which renderer
 * style it uses, which colour tokens it overrides on the sheet, what type
 * treatment and layout grammar the engines are told to follow, and which
 * venues it suits. Templates never change the photo pipeline.
 */

export type TemplateTheme = Partial<Record<"--ink" | "--paper" | "--card" | "--press" | "--press-deep" | "--poster" | "--poster-foreground" | "--sun" | "--line" | "--muted" | "--herb", string>>;

export type Template = {
  id: string;
  name: string;
  tagline: string;
  /** Renderer style in MenuSheet. */
  style: StyleKey;
  mode: "poster" | "menu";
  /** Colour overrides applied on the sheet wrapper (CSS variables used by styles.css tokens). */
  theme: TemplateTheme;
  /** Human palette description for the engines and the UI. */
  palette: string[];
  typography: string;
  layout: string;
  photoTreatment: string;
  suits: { storeTypes: StoreType[]; priceTiers: ("budget" | "mid" | "premium" | "luxury")[]; cuisines: string[]; lighting: ("bright" | "neutral" | "dim" | "outdoor")[]; surfaces: string[] };
};

export const TEMPLATES: Template[] = [
  {
    id: "street-deals", name: "Street Deals", tagline: "Loud, photo-led takeaway board", style: "bold-deals", mode: "poster",
    theme: { "--poster": "oklch(0.57 0.24 28)", "--sun": "oklch(0.84 0.18 89)", "--ink": "oklch(0.2 0.02 60)" },
    palette: ["yellow", "red", "black", "white"], typography: "Archivo Black display, condensed uppercase names, huge prices in red blocks",
    layout: "Headline zone, stacked offer rows with square photos, price block per offer, feature strip at the bottom",
    photoTreatment: "Full-bleed square dish photos, complete meal visible",
    suits: { storeTypes: ["takeaway", "food-truck", "kiosk-stall", "fast-casual"], priceTiers: ["budget", "mid"], cuisines: ["greek", "kebab", "pizza", "fish", "burgers", "fried chicken"], lighting: ["bright", "outdoor", "neutral"], surfaces: ["fascia", "wall-board", "window", "a-frame"] },
  },
  {
    id: "charcoal-grill", name: "Charcoal Grill", tagline: "Dark board, ember accents", style: "bold-deals", mode: "poster",
    theme: { "--poster": "oklch(0.62 0.2 40)", "--sun": "oklch(0.78 0.15 70)", "--ink": "oklch(0.17 0.01 40)", "--card": "oklch(0.26 0.015 45)", "--poster-foreground": "oklch(0.98 0.01 90)", "--muted": "oklch(0.78 0.02 70)", "--line": "oklch(0.35 0.02 45)" },
    palette: ["charcoal", "ember orange", "warm cream"], typography: "Heavy condensed display, warm cream text on charcoal",
    layout: "Dark stacked offers, glowing price blocks, photos with dark surrounds",
    photoTreatment: "Low-key lit grill photography, char and sear emphasised",
    suits: { storeTypes: ["takeaway", "casual-dining", "bar-pub", "food-truck"], priceTiers: ["mid", "premium"], cuisines: ["steak", "kebab", "greek", "burgers", "bbq", "fried chicken"], lighting: ["dim", "neutral"], surfaces: ["wall-board", "digital-screen", "fascia"] },
  },
  {
    id: "pizzeria-classic", name: "Pizzeria Classic", tagline: "Red, green, cream and a whole pie", style: "bold-deals", mode: "poster",
    theme: { "--poster": "oklch(0.52 0.21 27)", "--sun": "oklch(0.93 0.05 95)", "--herb": "oklch(0.5 0.14 150)", "--ink": "oklch(0.22 0.03 40)" },
    palette: ["tomato red", "basil green", "cream"], typography: "Bold condensed names, friendly rounded accents",
    layout: "Hero pie centred for signs, two-column pizza grid for boards, sizes and prices as a mini table",
    photoTreatment: "Overhead whole pies on dark steel, cheese stretch close-ups",
    suits: { storeTypes: ["takeaway", "casual-dining", "fast-casual"], priceTiers: ["budget", "mid"], cuisines: ["pizza", "italian"], lighting: ["bright", "neutral"], surfaces: ["fascia", "wall-board", "window", "counter-card"] },
  },
  {
    id: "kraft-cafe", name: "Kraft Café", tagline: "Warm paper, serif, breathing room", style: "editorial", mode: "menu",
    theme: { "--paper": "oklch(0.93 0.03 80)", "--card": "oklch(0.97 0.02 85)", "--press": "oklch(0.5 0.12 45)", "--press-deep": "oklch(0.4 0.1 40)", "--ink": "oklch(0.25 0.03 50)" },
    palette: ["kraft", "espresso brown", "cream"], typography: "Fraunces serif names, Inter body, small mono labels",
    layout: "Sections with dotted leaders, coffee and food separated, hero banner optional",
    photoTreatment: "Daylight café shots, white ceramic, light oak",
    suits: { storeTypes: ["cafe", "bakery-dessert"], priceTiers: ["mid", "premium"], cuisines: ["cafe", "brunch", "bakery", "coffee"], lighting: ["bright", "neutral"], surfaces: ["wall-board", "table-menu", "a-frame", "counter-card"] },
  },
  {
    id: "botanical-brunch", name: "Botanical Brunch", tagline: "Sage, blush and airy type", style: "modern", mode: "menu",
    theme: { "--paper": "oklch(0.96 0.015 120)", "--card": "oklch(0.985 0.01 120)", "--press": "oklch(0.52 0.1 150)", "--press-deep": "oklch(0.42 0.09 150)", "--herb": "oklch(0.55 0.12 150)", "--line": "oklch(0.88 0.03 120)" },
    palette: ["sage", "blush", "off-white"], typography: "Geometric sans, uppercase section labels, light weights",
    layout: "Two-column grid, generous margins, dietary icons only if the brief supplies them",
    photoTreatment: "Overhead bowls, bright natural light",
    suits: { storeTypes: ["cafe", "casual-dining"], priceTiers: ["mid", "premium"], cuisines: ["brunch", "salads", "healthy", "cafe", "vegan"], lighting: ["bright", "neutral"], surfaces: ["table-menu", "wall-board", "handout"] },
  },
  {
    id: "chalkboard-pub", name: "Chalkboard Pub", tagline: "Dark board, chalk-white type", style: "rustic", mode: "menu",
    theme: { "--paper": "oklch(0.22 0.01 60)", "--card": "oklch(0.25 0.012 60)", "--ink": "oklch(0.95 0.01 90)", "--press": "oklch(0.82 0.14 85)", "--press-deep": "oklch(0.75 0.14 80)", "--line": "oklch(0.4 0.015 60)", "--muted": "oklch(0.78 0.02 80)", "--herb": "oklch(0.75 0.1 150)" },
    palette: ["blackboard", "chalk white", "mustard"], typography: "Slab and mono labels, italic serif item names",
    layout: "Drinks grid with size columns, food in three sections, specials panel",
    photoTreatment: "Minimal photos; one hero at most",
    suits: { storeTypes: ["bar-pub", "casual-dining", "cafe"], priceTiers: ["budget", "mid"], cuisines: ["pub", "burgers", "steak", "drinks", "bar"], lighting: ["dim", "neutral"], surfaces: ["wall-board", "a-frame", "digital-screen", "table-menu"] },
  },
  {
    id: "noir-fine", name: "Noir Fine", tagline: "Ivory, hairlines and silence", style: "editorial", mode: "menu",
    theme: { "--paper": "oklch(0.97 0.01 90)", "--card": "oklch(0.985 0.005 90)", "--press": "oklch(0.3 0.02 60)", "--press-deep": "oklch(0.2 0.02 60)", "--ink": "oklch(0.18 0.01 60)", "--line": "oklch(0.9 0.01 90)", "--muted": "oklch(0.5 0.02 60)" },
    palette: ["ivory", "charcoal", "one muted accent"], typography: "Light serif, wide tracking, small caps section names",
    layout: "Single column, courses as sections, prices right-aligned without currency symbols where the brief allows",
    photoTreatment: "No photos, or one restrained still life",
    suits: { storeTypes: ["fine-dining", "casual-dining", "bar-pub"], priceTiers: ["premium", "luxury"], cuisines: ["modern", "french", "italian", "japanese", "steak", "seafood"], lighting: ["dim", "neutral"], surfaces: ["table-menu", "handout"] },
  },
  {
    id: "sushi-slate", name: "Sushi Slate", tagline: "Slate grey, vermilion, grid", style: "modern", mode: "menu",
    theme: { "--paper": "oklch(0.3 0.01 250)", "--card": "oklch(0.34 0.012 250)", "--ink": "oklch(0.96 0.005 90)", "--press": "oklch(0.6 0.2 30)", "--press-deep": "oklch(0.5 0.18 30)", "--line": "oklch(0.45 0.01 250)", "--muted": "oklch(0.8 0.01 250)" },
    palette: ["slate", "vermilion", "paper white"], typography: "Clean sans with a vermilion accent rule, numbers aligned",
    layout: "Dense but ordered grid: nigiri, rolls, hot dishes as columns; photo tiles square",
    photoTreatment: "Dark ceramic, glossy fish, overhead",
    suits: { storeTypes: ["casual-dining", "takeaway", "fast-casual"], priceTiers: ["mid", "premium"], cuisines: ["japanese", "sushi", "ramen", "korean", "asian"], lighting: ["neutral", "dim"], surfaces: ["wall-board", "digital-screen", "table-menu"] },
  },
  {
    id: "spice-market", name: "Spice Market", tagline: "Saffron, deep red, brass", style: "rustic", mode: "menu",
    theme: { "--paper": "oklch(0.93 0.04 70)", "--card": "oklch(0.97 0.025 75)", "--press": "oklch(0.55 0.2 30)", "--press-deep": "oklch(0.45 0.18 28)", "--sun": "oklch(0.8 0.17 75)", "--ink": "oklch(0.24 0.04 40)" },
    palette: ["saffron", "deep red", "brass"], typography: "Mono section labels, serif item names, ornamental rule",
    layout: "Sections per course with heat markers only if supplied, curries in a two-column grid",
    photoTreatment: "Copper serveware, overhead with slight tilt",
    suits: { storeTypes: ["casual-dining", "takeaway", "fast-casual"], priceTiers: ["budget", "mid"], cuisines: ["indian", "curry", "middle eastern", "kebab", "thai"], lighting: ["neutral", "dim", "bright"], surfaces: ["wall-board", "table-menu", "window", "handout"] },
  },
  {
    id: "taqueria-pop", name: "Taquería Pop", tagline: "Pink, teal, lime; playful blocks", style: "bold-deals", mode: "poster",
    theme: { "--poster": "oklch(0.62 0.22 350)", "--sun": "oklch(0.86 0.16 110)", "--herb": "oklch(0.6 0.12 190)", "--ink": "oklch(0.22 0.03 300)" },
    palette: ["hot pink", "teal", "lime"], typography: "Chunky display, tilted badges, hand-cut shapes",
    layout: "Colour-blocked offer tiles, tacos in rows, combos boxed",
    photoTreatment: "Tacos in a row on a wooden board, lime wedges",
    suits: { storeTypes: ["takeaway", "food-truck", "fast-casual", "kiosk-stall"], priceTiers: ["budget", "mid"], cuisines: ["mexican", "tacos", "burritos", "latin"], lighting: ["bright", "outdoor", "neutral"], surfaces: ["fascia", "a-frame", "wall-board", "window"] },
  },
  {
    id: "smash-house", name: "Smash House", tagline: "Black, mustard, kraft; burger-first", style: "bold-deals", mode: "poster",
    theme: { "--poster": "oklch(0.2 0.01 60)", "--poster-foreground": "oklch(0.85 0.17 85)", "--sun": "oklch(0.85 0.17 85)", "--ink": "oklch(0.18 0.01 60)", "--card": "oklch(0.95 0.03 80)" },
    palette: ["black", "mustard", "kraft"], typography: "Ultra-bold condensed, numbers huge",
    layout: "Hero burger left, combos right, prices in mustard blocks",
    photoTreatment: "Eye-level burgers on kraft paper, 85 mm look",
    suits: { storeTypes: ["fast-casual", "takeaway", "food-truck", "bar-pub"], priceTiers: ["budget", "mid"], cuisines: ["burgers", "fried chicken", "american", "bbq"], lighting: ["bright", "neutral", "dim"], surfaces: ["wall-board", "digital-screen", "fascia", "window"] },
  },
  {
    id: "harbour-fish", name: "Harbour Fish", tagline: "Navy, white, lemon; coastal takeaway", style: "bold-deals", mode: "poster",
    theme: { "--poster": "oklch(0.38 0.12 255)", "--sun": "oklch(0.9 0.14 95)", "--ink": "oklch(0.22 0.04 255)", "--press": "oklch(0.45 0.13 255)", "--press-deep": "oklch(0.35 0.12 255)" },
    palette: ["navy", "white", "lemon"], typography: "Bold sans, rounded badges, striped rule accents",
    layout: "Packs and family deals as rows, single items in a two-column grid",
    photoTreatment: "Paper-lined trays, lemon halves, crisp batter",
    suits: { storeTypes: ["takeaway", "kiosk-stall", "casual-dining"], priceTiers: ["budget", "mid"], cuisines: ["fish", "seafood", "fish & chips"], lighting: ["bright", "outdoor", "neutral"], surfaces: ["fascia", "wall-board", "window", "a-frame"] },
  },
  {
    id: "patisserie-blush", name: "Patisserie Blush", tagline: "Blush, cocoa, gold hairlines", style: "editorial", mode: "menu",
    theme: { "--paper": "oklch(0.96 0.02 20)", "--card": "oklch(0.985 0.012 20)", "--press": "oklch(0.45 0.08 40)", "--press-deep": "oklch(0.35 0.07 40)", "--ink": "oklch(0.28 0.05 40)", "--line": "oklch(0.88 0.03 30)" },
    palette: ["blush", "cocoa", "gold"], typography: "Elegant serif, small caps, thin gold rules",
    layout: "Product list with sizes, cakes by the slice/whole as a small table",
    photoTreatment: "Macro texture on marble, soft light",
    suits: { storeTypes: ["bakery-dessert", "cafe"], priceTiers: ["mid", "premium", "luxury"], cuisines: ["bakery", "dessert", "cake", "pastry", "gelato"], lighting: ["bright", "neutral"], surfaces: ["counter-card", "wall-board", "window", "handout"] },
  },
  {
    id: "screen-grid", name: "Screen Grid", tagline: "Digital board, 16:9, high contrast", style: "modern", mode: "poster",
    theme: { "--paper": "oklch(0.15 0.01 260)", "--card": "oklch(0.2 0.012 260)", "--ink": "oklch(0.97 0.005 90)", "--poster": "oklch(0.75 0.17 60)", "--poster-foreground": "oklch(0.15 0.01 260)", "--sun": "oklch(0.75 0.17 60)", "--line": "oklch(0.3 0.01 260)", "--muted": "oklch(0.75 0.01 260)" },
    palette: ["near-black", "amber", "white"], typography: "Bold sans readable from 4-8 m, prices aligned right",
    layout: "Three columns, category headers, photo tiles for heroes only",
    photoTreatment: "Square tiles, consistent lighting across all photos",
    suits: { storeTypes: ["fast-casual", "takeaway", "bar-pub", "ghost-kitchen"], priceTiers: ["budget", "mid"], cuisines: [], lighting: ["bright", "neutral", "dim"], surfaces: ["digital-screen", "drive-thru-board"] },
  },
  {
    id: "truck-bold", name: "Truck Bold", tagline: "Six items, huge type, one hero", style: "bold-deals", mode: "poster",
    theme: { "--poster": "oklch(0.55 0.22 20)", "--sun": "oklch(0.88 0.17 95)", "--ink": "oklch(0.2 0.02 40)" },
    palette: ["cherry red", "yellow", "black"], typography: "Giant condensed names, prices same size as names",
    layout: "One hero photo, up to six items in two columns, no descriptions longer than six words",
    photoTreatment: "Hero only, 45-degree, saturated but real",
    suits: { storeTypes: ["food-truck", "kiosk-stall", "takeaway"], priceTiers: ["budget", "mid"], cuisines: [], lighting: ["outdoor", "bright"], surfaces: ["fascia", "a-frame", "wall-board"] },
  },
  {
    id: "mono-modern", name: "Mono Modern", tagline: "Black on white, one accent, strict grid", style: "modern", mode: "menu",
    theme: { "--paper": "oklch(0.99 0 0)", "--card": "oklch(1 0 0)", "--press": "oklch(0.5 0.2 260)", "--press-deep": "oklch(0.4 0.18 260)", "--ink": "oklch(0.15 0 0)", "--line": "oklch(0.9 0 0)" },
    palette: ["white", "black", "electric blue"], typography: "Grotesk sans, uppercase labels, tight grid",
    layout: "Strict columns, numbered items optional, no ornaments",
    photoTreatment: "Few photos, square, neutral backgrounds",
    suits: { storeTypes: ["fast-casual", "cafe", "ghost-kitchen", "casual-dining"], priceTiers: ["mid", "premium"], cuisines: [], lighting: ["bright", "neutral"], surfaces: ["wall-board", "digital-screen", "handout", "table-menu"] },
  },
];

export type TemplateMatch = { template: Template; score: number; reasons: string[] };

function cuisineWords(profile: StoreProfile) {
  return `${profile.cuisine} ${profile.signatureItems.join(" ")}`.toLowerCase();
}

/** Scores every template against a profile and returns the best matches with plain-language reasons. */
export function recommendTemplates(profile: StoreProfile, limit = 4): TemplateMatch[] {
  const cuisine = cuisineWords(profile);
  const guide = STORE_TYPE_GUIDE[profile.storeType];
  const matches = TEMPLATES.map((template) => {
    let score = 0;
    const reasons: string[] = [];
    if (template.suits.storeTypes.includes(profile.storeType)) { score += 40; reasons.push(`Made for ${guide.label.toLowerCase()} venues`); }
    if (template.suits.priceTiers.includes(profile.priceTier)) { score += 15; reasons.push(`Fits a ${profile.priceTier} price point`); }
    const cuisineHit = template.suits.cuisines.find((word) => cuisine.includes(word));
    if (cuisineHit) { score += 25 + (template.suits.cuisines.length <= 3 ? 10 : 0); reasons.push(`Designed around ${cuisineHit}`); }
    else if (template.suits.cuisines.length === 0) { score += 8; }
    if (template.suits.lighting.includes(profile.lighting)) { score += 8; reasons.push(`Reads well in ${profile.lighting} light`); }
    const surfaceHit = profile.displaySurfaces.find((surface) => template.suits.surfaces.includes(surface));
    if (surfaceHit) { score += 12; reasons.push(`Suits your ${surfaceHit.replace("-", " ")}`); }
    const vibeHits = template.palette.concat(template.tagline.toLowerCase().split(/\W+/)).filter((word) => profile.vibe.includes(word));
    if (vibeHits.length) { score += 5; reasons.push(`Matches the ${vibeHits[0]} feel`); }
    if (profile.palette.length && template.palette.some((name) => /dark|black|charcoal|slate|navy|blackboard|near-black/.test(name)) && profile.palette.some((hex) => isDark(hex))) { score += 6; reasons.push("Works with your dark brand colours"); }
    return { template, score, reasons: reasons.slice(0, 3) };
  });
  return matches.sort((a, b) => b.score - a.score).slice(0, limit);
}

function isDark(hex: string) {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return false;
  const value = Number.parseInt(match[1] ?? "000000", 16);
  const r = (value >> 16) & 255, g = (value >> 8) & 255, b = value & 255;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 90;
}

export function templateById(id: string | undefined) {
  return TEMPLATES.find((template) => template.id === id);
}

/** Prompt block describing a template for the engines. */
export function describeTemplate(template: Template) {
  return [
    `TEMPLATE "${template.name}" (${template.id}): ${template.tagline}.`,
    `Palette: ${template.palette.join(", ")}. Typography: ${template.typography}.`,
    `Layout: ${template.layout}. Photo treatment: ${template.photoTreatment}.`,
    "Write copy, badges, taglines and photo briefs that belong to this template. Never change exact names, prices or counts to fit it.",
  ].join("\n");
}

```

===== FILE: src/lib/ai/display.ts =====
```tsx
import { z } from "zod/v4";

import type { GeneratedMenuCore, OutputSize } from "./schema";

/**
 * Display measurement and menu fitting.
 *
 * A photo of the display (wall board, screen, window, A-frame, fascia) is sent
 * to a vision engine that returns the structured measurement below: what the
 * display is, where its corners are in the image, how big it is relative to
 * reference objects in the scene, and how far people read it from. Everything
 * after that is deterministic: aspect ratio from the corners (with a
 * perspective correction), nearest print format or a custom one, letter-height
 * minimums from the viewing distance, and a content budget the menu is fitted
 * to.
 */

export const DISPLAY_TYPES = ["wall-board", "digital-screen", "window", "counter-card", "a-frame", "fascia", "table-menu", "drive-thru-board", "unknown"] as const;
export type DisplayType = (typeof DISPLAY_TYPES)[number];
export type Orientation = "ultra-wide" | "landscape" | "square" | "portrait" | "tall";

/** Real-world sizes of things that commonly appear in venue photos (cm). */
export const REFERENCE_OBJECTS: Record<string, { widthCm?: number; heightCm?: number; note: string }> = {
  "interior door": { widthCm: 82, heightCm: 204, note: "Standard hinged door; height is the most reliable" },
  "shop door": { widthCm: 90, heightCm: 210, note: "Commercial entrance door leaf" },
  "a4 sheet": { widthCm: 21, heightCm: 29.7, note: "Printed notice or poster" },
  "a3 sheet": { widthCm: 29.7, heightCm: 42, note: "" },
  "brick": { widthCm: 23, heightCm: 7.6, note: "Face brick; count courses for height" },
  "ceiling tile": { widthCm: 60, heightCm: 60, note: "Suspended ceiling grid" },
  "light switch": { widthCm: 8.6, heightCm: 8.6, note: "AU/UK plate" },
  "power outlet": { widthCm: 11.5, heightCm: 7, note: "AU double outlet plate" },
  "counter height": { heightCm: 90, note: "Service counter top from floor" },
  "bar height": { heightCm: 105, note: "Bar top from floor" },
  "chair seat": { heightCm: 45, note: "Dining chair seat from floor" },
  "table top": { heightCm: 75, note: "Dining table from floor" },
  "43 inch tv": { widthCm: 96, heightCm: 54, note: "16:9 panel, bezel excluded" },
  "50 inch tv": { widthCm: 111, heightCm: 62, note: "" },
  "55 inch tv": { widthCm: 122, heightCm: 69, note: "" },
  "65 inch tv": { widthCm: 144, heightCm: 81, note: "" },
  "person": { heightCm: 170, note: "Average adult standing" },
  "soft drink can": { widthCm: 6.6, heightCm: 12.3, note: "375 ml" },
  "2 litre bottle": { heightCm: 32, note: "" },
  "takeaway coffee cup": { heightCm: 12, note: "12 oz" },
  "floor tile": { widthCm: 60, heightCm: 60, note: "Common commercial tile; 30 cm also common" },
};

export const displayMeasurementSchema = z.object({
  displayType: z.enum(DISPLAY_TYPES),
  /** Four corners of the display in the image, normalised 0-1, order: top-left, top-right, bottom-right, bottom-left. */
  corners: z.array(z.object({ x: z.number(), y: z.number() })),
  /** Physical size estimate in cm; 0 when unknown. */
  estimatedWidthCm: z.number(),
  estimatedHeightCm: z.number(),
  /** Reference objects used and their assumed sizes. */
  references: z.array(z.object({ object: z.string(), assumedCm: z.number(), pixelsRelativeToDisplay: z.number() })),
  /** Typical reading distance in metres (counter ≈ 2, across a room ≈ 5, street ≈ 12). */
  viewingDistanceM: z.number(),
  mountedHeightCm: z.number(),
  lighting: z.enum(["bright", "neutral", "dim", "outdoor", "backlit"]),
  isDigital: z.boolean(),
  existingContentSummary: z.string(),
  confidence: z.number(),
  notes: z.string(),
});
export type DisplayMeasurement = z.infer<typeof displayMeasurementSchema>;

export const DISPLAY_MEASUREMENT_JSON_SCHEMA = {
  type: "object",
  properties: {
    displayType: { type: "string", enum: [...DISPLAY_TYPES] },
    corners: { type: "array", items: { type: "object", properties: { x: { type: "number" }, y: { type: "number" } }, required: ["x", "y"], additionalProperties: false } },
    estimatedWidthCm: { type: "number" },
    estimatedHeightCm: { type: "number" },
    references: { type: "array", items: { type: "object", properties: { object: { type: "string" }, assumedCm: { type: "number" }, pixelsRelativeToDisplay: { type: "number" } }, required: ["object", "assumedCm", "pixelsRelativeToDisplay"], additionalProperties: false } },
    viewingDistanceM: { type: "number" },
    mountedHeightCm: { type: "number" },
    lighting: { type: "string", enum: ["bright", "neutral", "dim", "outdoor", "backlit"] },
    isDigital: { type: "boolean" },
    existingContentSummary: { type: "string" },
    confidence: { type: "number" },
    notes: { type: "string" },
  },
  required: ["displayType", "corners", "estimatedWidthCm", "estimatedHeightCm", "references", "viewingDistanceM", "mountedHeightCm", "lighting", "isDigital", "existingContentSummary", "confidence", "notes"],
  additionalProperties: false,
} as const;

export const DISPLAY_MEASUREMENT_PROMPT = [
  "You are a signage surveyor. The attached photo shows a menu display (board, screen, window, A-frame, fascia, counter card). Measure it.",
  "corners: the four corners of the display surface itself (not its frame) as normalised image coordinates 0-1, in the order top-left, top-right, bottom-right, bottom-left. If a corner is cut off by the photo edge, extrapolate and say so in notes.",
  `estimatedWidthCm / estimatedHeightCm: physical size of the display. Use reference objects at a similar depth in the scene and list each one you used with the size you assumed. Known sizes: ${Object.entries(REFERENCE_OBJECTS).map(([name, size]) => `${name} ${size.widthCm ? `${size.widthCm}w` : ""}${size.widthCm && size.heightCm ? "×" : ""}${size.heightCm ? `${size.heightCm}h` : ""} cm`).join("; ")}. For a TV, read the bezel and panel proportions and pick the closest standard diagonal. Return 0 for a dimension you cannot estimate.`,
  "viewingDistanceM: how far a customer typically stands when reading it (behind a counter ≈ 2 m, across a room ≈ 4-6 m, from the street ≈ 8-15 m). mountedHeightCm: bottom edge above floor, 0 if unknown.",
  "existingContentSummary: in one or two sentences, what the display currently shows (sections, rough item count, photo use), so the new design can respect what works.",
  "confidence 0-1 reflects how reliable the size estimate is. Be honest: a photo with no reference objects deserves confidence ≤ 0.3.",
  "Return JSON only.",
].join("\n");

// ---------- geometry ----------

type Point = { x: number; y: number };

function distance(a: Point, b: Point) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * Aspect ratio (width / height) of a quadrilateral seen in perspective.
 * Uses the mean of opposite edges, then corrects for foreshortening with a
 * simple vanishing-angle heuristic: the more the top and bottom edges differ
 * in length, the more the camera was tilted vertically (and vice versa).
 */
export function aspectFromCorners(corners: Point[], imageAspect = 4 / 3): number | null {
  if (corners.length !== 4) return null;
  const [tl, tr, br, bl] = corners as [Point, Point, Point, Point];
  // Work in image pixel space so x and y are comparable.
  const scale = (p: Point) => ({ x: p.x * imageAspect, y: p.y });
  const a = scale(tl), b = scale(tr), c = scale(br), d = scale(bl);
  const top = distance(a, b), bottom = distance(d, c), left = distance(a, d), right = distance(b, c);
  if (!top || !bottom || !left || !right) return null;
  // Foreshortening: if one pair of opposite sides differs a lot, the true length is closer to the longer one.
  const width = Math.max(top, bottom) * 0.6 + Math.min(top, bottom) * 0.4;
  const height = Math.max(left, right) * 0.6 + Math.min(left, right) * 0.4;
  const ratio = width / height;
  return Number.isFinite(ratio) && ratio > 0.05 && ratio < 20 ? ratio : null;
}

export function orientationOf(aspect: number): Orientation {
  if (aspect >= 2.6) return "ultra-wide";
  if (aspect >= 1.2) return "landscape";
  if (aspect > 0.85) return "square";
  if (aspect > 0.45) return "portrait";
  return "tall";
}

export const KNOWN_FORMATS: { key: OutputSize; widthCm: number; heightCm: number; label: string }[] = [
  { key: "fascia-447", widthCm: 447, heightCm: 71.5, label: "Fascia 447 × 71.5 cm" },
  { key: "fascia-180", widthCm: 180, heightCm: 60.4, label: "Board 180 × 60.4 cm" },
  { key: "wide", widthCm: 160, heightCm: 90, label: "Wide board 16:9" },
  { key: "poster", widthCm: 59.4, heightCm: 84.1, label: "Portrait poster A1" },
  { key: "square", widthCm: 30.5, heightCm: 30.5, label: "12-inch square sign" },
  { key: "letter", widthCm: 21.6, heightCm: 27.9, label: "US Letter" },
  { key: "a5", widthCm: 14.8, heightCm: 21, label: "A5" },
];

export type ContentBudget = {
  /** Smallest readable cap height for item names at the viewing distance, cm. */
  minLetterHeightCm: number;
  maxOffers: number;
  maxItems: number;
  maxSections: number;
  maxNameWords: number;
  maxDescriptionWords: number;
  photosRecommended: number;
};

export type DisplaySpec = {
  displayType: DisplayType;
  widthCm: number;
  heightCm: number;
  aspect: number;
  orientation: Orientation;
  /** Existing renderer format when one is close enough, otherwise "custom". */
  format: OutputSize | "custom";
  formatLabel: string;
  viewingDistanceM: number;
  lighting: DisplayMeasurement["lighting"];
  isDigital: boolean;
  sizeConfidence: number;
  budget: ContentBudget;
  sizeSource: "user" | "model" | "aspect-only";
  notes: string[];
};

export type DisplayOverrides = { widthCm?: number | undefined; heightCm?: number | undefined; viewingDistanceM?: number | undefined; imageAspect?: number | undefined };

/** Letter height rule of thumb: ~2.5 cm of cap height per 3 m of viewing distance; floor 0.3 cm (≈12 pt) for hand-held menus. */
export function minLetterHeightCm(viewingDistanceM: number) {
  return Math.max(0.3, Math.round((viewingDistanceM / 3) * 2.5 * 10) / 10);
}

export function contentBudget(widthCm: number, heightCm: number, viewingDistanceM: number, orientation: Orientation, digital: boolean): ContentBudget {
  const letter = minLetterHeightCm(viewingDistanceM);
  // A text line needs roughly 2.2× the cap height (ascenders, descenders, leading).
  const lineCm = letter * 2.2;
  const usableHeight = heightCm * 0.72; // minus brand zone, margins, footer
  const rows = Math.max(1, Math.floor(usableHeight / lineCm));
  const charsPerLine = Math.max(10, Math.floor(widthCm / (letter * 0.6)));
  // How many text columns the width supports at this letter size.
  const textColumns = orientation === "ultra-wide"
    ? Math.min(6, Math.max(2, Math.floor(widthCm / (letter * 12))))
    : orientation === "landscape"
      ? Math.min(3, Math.max(1, Math.floor(widthCm / (letter * 26))))
      : charsPerLine >= 70 ? 2 : 1;
  // Lines per item: name plus a short description, less on ultra-wide where descriptions are rare.
  const linesPerItem = orientation === "ultra-wide" ? 1.2 : 1.6;
  const linesPerOffer = orientation === "ultra-wide" ? 2.5 : 5; // photo-led offer row
  const maxItems = Math.max(2, Math.min(40, Math.floor(rows / linesPerItem) * textColumns));
  const offerColumns = orientation === "ultra-wide" ? Math.min(5, Math.max(2, Math.floor(widthCm / (letter * 14)))) : orientation === "landscape" ? Math.min(3, Math.max(1, Math.floor(widthCm / (letter * 30)))) : 1;
  const maxOffers = Math.max(1, Math.min(6, Math.max(1, Math.floor(rows / linesPerOffer)) * offerColumns));
  const maxNameWords = Math.max(2, Math.min(6, Math.floor(charsPerLine / (textColumns * 9))));
  const maxDescriptionWords = orientation === "ultra-wide" ? Math.min(8, Math.max(0, Math.floor(charsPerLine / (textColumns * 7)))) : Math.min(14, Math.max(4, Math.floor(charsPerLine / (textColumns * 6))));
  const maxSections = Math.max(1, Math.min(8, Math.floor(maxItems / 4)));
  const photosRecommended = digital || orientation === "ultra-wide" ? Math.min(maxOffers, 4) : Math.min(maxOffers, 3);
  return { minLetterHeightCm: letter, maxOffers, maxItems, maxSections, maxNameWords, maxDescriptionWords, photosRecommended };
}

/** Turns a raw measurement (plus optional user-entered sizes) into the spec the studio and the engines use. */
export function resolveDisplay(measurement: Partial<DisplayMeasurement> | undefined, overrides: DisplayOverrides = {}): DisplaySpec {
  const notes: string[] = [];
  const cornersAspect = measurement?.corners ? aspectFromCorners(measurement.corners, overrides.imageAspect ?? 4 / 3) : null;
  let widthCm = overrides.widthCm ?? 0;
  let heightCm = overrides.heightCm ?? 0;
  let sizeSource: DisplaySpec["sizeSource"] = "aspect-only";
  if (widthCm > 0 && heightCm > 0) sizeSource = "user";
  else if (widthCm > 0 || heightCm > 0) {
    // One user dimension plus an aspect from the photo or the model.
    const aspect = cornersAspect ?? (measurement?.estimatedWidthCm && measurement.estimatedHeightCm ? measurement.estimatedWidthCm / measurement.estimatedHeightCm : 16 / 9);
    if (widthCm > 0) heightCm = widthCm / aspect; else widthCm = heightCm * aspect;
    sizeSource = "user";
    notes.push("One dimension was supplied; the other was derived from the photo's aspect ratio.");
  } else if (measurement?.estimatedWidthCm && measurement.estimatedHeightCm) {
    widthCm = measurement.estimatedWidthCm;
    heightCm = measurement.estimatedHeightCm;
    sizeSource = "model";
    if (cornersAspect && Math.abs(cornersAspect - widthCm / heightCm) / cornersAspect > 0.25) {
      notes.push(`The corner geometry suggests an aspect of ${cornersAspect.toFixed(2)} while the size estimate implies ${(widthCm / heightCm).toFixed(2)}; the geometry was trusted for the shape, the estimate for the scale.`);
      const area = widthCm * heightCm;
      widthCm = Math.sqrt(area * cornersAspect);
      heightCm = area / widthCm;
    }
  } else if (cornersAspect) {
    // Shape only: assume a plausible size for the display type so budgets are sensible.
    const assumed: Record<DisplayType, number> = { "wall-board": 120, "digital-screen": 122, window: 100, "counter-card": 21, "a-frame": 60, fascia: 400, "table-menu": 21, "drive-thru-board": 150, unknown: 100 };
    widthCm = assumed[measurement?.displayType ?? "unknown"];
    heightCm = widthCm / cornersAspect;
    notes.push("No size could be estimated; a typical size for this display type was assumed. Enter the real width or height for exact fitting.");
  } else {
    widthCm = 160; heightCm = 90;
    notes.push("No measurement available; defaulting to a 16:9 wide board.");
  }
  widthCm = Math.round(widthCm * 10) / 10;
  heightCm = Math.round(heightCm * 10) / 10;
  const aspect = widthCm / heightCm;
  const orientation = orientationOf(aspect);
  const viewingDistanceM = overrides.viewingDistanceM ?? (measurement?.viewingDistanceM && measurement.viewingDistanceM > 0 ? measurement.viewingDistanceM : defaultViewingDistance(measurement?.displayType ?? "unknown", orientation));
  const digital = Boolean(measurement?.isDigital) || measurement?.displayType === "digital-screen";

  // Nearest known format: same orientation and within 12% on aspect and 25% on width.
  let format: DisplaySpec["format"] = "custom";
  let formatLabel = `Custom ${widthCm} × ${heightCm} cm`;
  for (const known of KNOWN_FORMATS) {
    const knownAspect = known.widthCm / known.heightCm;
    if (Math.abs(knownAspect - aspect) / knownAspect <= 0.12 && (sizeSource === "aspect-only" || Math.abs(known.widthCm - widthCm) / known.widthCm <= 0.25)) {
      format = known.key;
      formatLabel = known.label;
      break;
    }
  }
  const budget = contentBudget(widthCm, heightCm, viewingDistanceM, orientation, digital);
  if (measurement?.notes) notes.push(measurement.notes);
  return {
    displayType: measurement?.displayType ?? "unknown",
    widthCm,
    heightCm,
    aspect: Math.round(aspect * 1000) / 1000,
    orientation,
    format,
    formatLabel,
    viewingDistanceM,
    lighting: measurement?.lighting ?? "neutral",
    isDigital: digital,
    sizeConfidence: sizeSource === "user" ? 1 : sizeSource === "model" ? Math.min(1, Math.max(0, measurement?.confidence ?? 0.3)) : 0.2,
    budget,
    sizeSource,
    notes,
  };
}

function defaultViewingDistance(type: DisplayType, orientation: Orientation) {
  switch (type) {
    case "fascia": return 10;
    case "drive-thru-board": return 3;
    case "digital-screen": return 4;
    case "wall-board": return orientation === "ultra-wide" ? 5 : 3.5;
    case "window": return 2.5;
    case "a-frame": return 2;
    case "counter-card": return 0.8;
    case "table-menu": return 0.5;
    default: return orientation === "ultra-wide" ? 6 : 3;
  }
}

/** Lines the engines receive so they design for the real display. */
export function displayBriefLines(spec: DisplaySpec) {
  const b = spec.budget;
  return [
    `DISPLAY: ${spec.displayType.replace("-", " ")}, ${spec.widthCm} × ${spec.heightCm} cm (${spec.orientation}, aspect ${spec.aspect}), ${spec.isDigital ? "digital screen" : "printed"}, read from about ${spec.viewingDistanceM} m in ${spec.lighting} light.`,
    `LEGIBILITY BUDGET: item names need ≥ ${b.minLetterHeightCm} cm cap height, so the design may hold at most ${b.maxItems} items (${b.maxOffers} photo-led offers) across ≤ ${b.maxSections} sections; names ≤ ${b.maxNameWords} words, descriptions ≤ ${b.maxDescriptionWords} words${b.maxDescriptionWords === 0 ? " (no descriptions)" : ""}; ${b.photosRecommended} photos at most.`,
    "If the supplied menu has more items than fit, keep the exact items the brief marks as priorities, then the first items of each section, and list what you left out in 'followed' as 'Omitted for legibility: …'. Never shrink type below the budget to squeeze items in.",
  ];
}

export type FitResult = { menu: GeneratedMenuCore; omitted: string[]; trimmedDescriptions: number; changed: boolean };

/**
 * Deterministic last-mile fit: enforces the budget on a finished menu so the
 * rendered design is legible even if an engine ignored the numbers.
 */
export function fitMenuToDisplay(menu: GeneratedMenuCore, spec: DisplaySpec): FitResult {
  const b = spec.budget;
  const omitted: string[] = [];
  let trimmed = 0;
  let changed = false;
  const limit = menu.mode === "poster" ? b.maxOffers : b.maxItems;
  let remaining = limit;
  const sections = menu.sections.slice(0, Math.max(1, b.maxSections)).map((section) => {
    const keep = section.items.slice(0, Math.max(0, remaining));
    const drop = section.items.slice(keep.length);
    remaining -= keep.length;
    omitted.push(...drop.map((item) => item.name));
    const items = keep.map((item) => {
      const words = item.description.trim().split(/\s+/).filter(Boolean);
      if (b.maxDescriptionWords === 0 && item.description) { trimmed++; return { ...item, description: "" }; }
      if (words.length > b.maxDescriptionWords) { trimmed++; return { ...item, description: `${words.slice(0, b.maxDescriptionWords).join(" ").replace(/[,;:]$/, "")}` }; }
      return item;
    });
    return { ...section, items };
  });
  for (const section of menu.sections.slice(sections.length)) omitted.push(...section.items.map((item) => item.name));
  const kept = sections.filter((section) => section.items.length > 0);
  changed = omitted.length > 0 || trimmed > 0 || kept.length !== menu.sections.length;
  const followed = omitted.length ? [...menu.followed.filter((line) => !line.startsWith("Omitted for legibility")), `Omitted for legibility: ${omitted.join(", ")}`] : menu.followed;
  return { menu: changed ? { ...menu, sections: kept.length ? kept : menu.sections, followed } : menu, omitted, trimmedDescriptions: trimmed, changed };
}

```
