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
