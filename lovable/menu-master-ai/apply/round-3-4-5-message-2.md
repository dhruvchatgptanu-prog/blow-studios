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

===== FILE: src/lib/ai/color.ts =====
```tsx
/**
 * Colour maths for template themes: OKLCH (as written in styles.css and
 * templates.ts) → sRGB → WCAG contrast ratio. Used to guarantee every template
 * keeps text legible: 4.5:1 is the WCAG minimum for normal text, 7:1 the
 * enhanced level we want for anything read from a distance.
 */

export type RGB = { r: number; g: number; b: number };

/** Parses `oklch(L C H)` (L as 0-1 or percentage) or `#rrggbb`. Returns null for anything else. */
export function parseColor(input: string): RGB | null {
  const value = input.trim().toLowerCase();
  const hex = /^#([0-9a-f]{6})$/.exec(value);
  if (hex) {
    const n = Number.parseInt(hex[1] ?? "0", 16);
    return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255 };
  }
  const oklch = /^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)(?:deg)?\s*(?:\/\s*[\d.]+%?)?\s*\)$/.exec(value);
  if (!oklch) return null;
  let L = Number(oklch[1]);
  if (oklch[2] === "%") L /= 100;
  const C = Number(oklch[3]);
  const H = (Number(oklch[4]) * Math.PI) / 180;
  const a = C * Math.cos(H);
  const b = C * Math.sin(H);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  return {
    r: clamp(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    g: clamp(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    b: clamp(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  };
}

/** WCAG relative luminance from linear sRGB components. */
export function luminance(rgb: RGB) {
  return 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
}

/** WCAG contrast ratio between two colours (1 … 21). Null when either colour cannot be parsed. */
export function contrastRatio(foreground: string, background: string): number | null {
  const fg = parseColor(foreground), bg = parseColor(background);
  if (!fg || !bg) return null;
  const a = luminance(fg), b = luminance(bg);
  const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  return Math.round(ratio * 100) / 100;
}

/** The app's default token values (styles.css) so a theme that overrides only some tokens is checked against the rest. */
export const DEFAULT_TOKENS: Record<string, string> = {
  "--paper": "oklch(0.945 0.028 85)",
  "--card": "oklch(0.975 0.018 88)",
  "--ink": "oklch(0.23 0.03 55)",
  "--muted": "oklch(0.55 0.035 70)",
  "--line": "oklch(0.86 0.035 80)",
  "--press": "oklch(0.6 0.22 34)",
  "--press-deep": "oklch(0.47 0.19 30)",
  "--herb": "oklch(0.52 0.14 155)",
  "--poster": "oklch(0.57 0.24 28)",
  "--poster-foreground": "oklch(0.985 0.01 95)",
  "--sun": "oklch(0.84 0.18 89)",
};

export type ContrastReport = {
  inkOnPaper: number;
  inkOnCard: number;
  posterText: number;
  priceOnCard: number;
  /** Section labels and accents (text-press) on the card. */
  accentOnCard: number;
  mutedOnCard: number;
  /** Lowest of the ratios that carry reading text. */
  weakest: number;
  level: "AAA" | "AA" | "fail";
};

/** Checks the text/background pairs the sheet actually renders for a theme (overrides merged over the defaults). */
export function themeContrast(theme: Record<string, string | undefined>): ContrastReport {
  const tokens: Record<string, string> = { ...DEFAULT_TOKENS };
  for (const [key, value] of Object.entries(theme)) if (value) tokens[key] = value;
  const get = (key: string) => tokens[key] ?? DEFAULT_TOKENS[key] ?? "#000000";
  const ratio = (fg: string, bg: string) => contrastRatio(get(fg), get(bg)) ?? 1;
  const inkOnPaper = ratio("--ink", "--paper");
  const inkOnCard = ratio("--ink", "--card");
  const posterText = ratio("--poster-foreground", "--poster");
  const priceOnCard = ratio("--press-deep", "--card");
  const accentOnCard = ratio("--press", "--card");
  const mutedOnCard = ratio("--muted", "--card");
  // Accent labels are small but bold uppercase; treat 3:1 (large-text minimum) as their floor by weighting them up.
  const weakest = Math.min(inkOnPaper, inkOnCard, posterText, priceOnCard, accentOnCard * 1.5);
  return { inkOnPaper, inkOnCard, posterText, priceOnCard, accentOnCard, mutedOnCard, weakest: Math.round(weakest * 100) / 100, level: weakest >= 7 ? "AAA" : weakest >= 4.5 ? "AA" : "fail" };
}
```


===== FILE: src/lib/ai/knowledge.ts =====
```tsx
/**
 * Halftone knowledge base.
 *
 * Everything the menu engines know about menu design, format constraints,
 * menu psychology, brief compliance and food-photo prompt writing lives here
 * as plain data so it can be fed to EVERY engine (Lovable gateway, OpenAI,
 * Claude) and to the judge identically. The system prompt is composed from
 * these blocks at module load, so the prefix is byte-stable (good for caching).
 *
 * PHOTO STYLE LOCK: the look of the generated photographs is produced by
 * `src/routes/api/generate-image.ts` (PHOTO_DIRECTION) and the image model in
 * `src/lib/image-gateway.server.ts`. Nothing in this file changes that pipeline.
 * The photo grammar below only makes the per-dish prompts more precise so the
 * same photographic style gets better subject matter.
 */

export const BRIEF_COMPLIANCE_RULES = [
  "The written brief is the contract. Preserve exact restaurant and dish names, prices, sections, counts, currencies, language, spelling, capitalisation, phone numbers, quantities, inclusions and exclusions.",
  "If a menu image is attached, transcribe visible information accurately and keep it unless the written brief explicitly changes it; the written brief always wins over the image.",
  "Redesign an attached menu's layout and wording hierarchy; never duplicate its layout.",
  "Only invent plausible menu content where no content is supplied. Never invent a phone number, address, offer deadline, service, dietary promise, award or quality claim.",
  "If descriptions or photos are forbidden by the brief, respect that completely.",
  "Every price in the brief must appear exactly once per matching item, formatted the way the brief wrote it (same currency symbol, same decimals).",
  "Quantities inside a deal (e.g. '3 lamb yiros and large chips') must be restated in the item description so the customer sees exactly what is included.",
  "'followed' lists 3-6 specific instructions you actually followed, quoting the brief. Never claim to follow something the brief did not say.",
  "'features' holds only selling points the brief supports. If none are supported return an empty array.",
  "Dietary markers ((V), (VG), (GF), (DF), (NF), (H)) and allergen statements are legal information, not copy: reproduce each marker exactly as the brief attaches it, directly after the item name, and never add, infer, expand or drop one. If the brief gives an allergen statement ('allergens: ask staff', 'contains nuts'), carry it word for word in a footer line.",
] as const;

export const MENU_MODE_RULES = [
  "MODE MENU: a refined, readable traditional menu, not an advertising board. One page typically has 3-4 sections and 10-16 items; two pages can carry up to 8 sections and 30 items.",
  "Order sections the way a guest eats: starters/small plates, mains or signature category, grill/specials, sides, desserts, drinks. Keep the brief's own order if it states one.",
  "Descriptions: 6-14 words, ingredient-led, sensory but honest. No exclamation marks. No 'delicious', 'amazing', 'best'.",
  "Keep the chosen editorial, modern or rustic treatment; do not impose the poster aesthetic on a classic menu.",
  "Section notes are optional one-liners (e.g. 'Wrapped fresh to order'). Use null when there is nothing worth saying.",
] as const;

export const POSTER_MODE_RULES = [
  "MODE POSTER: a high-impact food promotion with exact inclusions, large prices and short offer labels; typically 2-4 offers unless the brief specifies a count.",
  "Each offer gets a short punchy badge (2-3 words, e.g. 'FEEDS 3', 'FAMILY FAVOURITE', 'BIG VALUE') that is true to the brief. Leave badge empty rather than inventing a claim.",
  "Tagline is the poster headline: 2-4 words, uppercase-friendly, no punctuation (e.g. 'FAMILY FEAST DEALS').",
  "Eyebrow is the strip above the headline: a short campaign line such as 'Fresh from the grill · this week only' ONLY if the brief supports a time limit; otherwise describe the category.",
  "The app renders all text separately from the photographs, so never request text, prices or logos inside photos.",
] as const;

export const FORMAT_RULES: Record<string, string> = {
  letter: "US Letter 8.5 × 11 in portrait. Comfortable margins, 3-4 sections, up to 16 items on one page.",
  a5: "A5 148 × 210 mm portrait. Tighter: 3 sections, up to 12 items, shorter descriptions (max 10 words).",
  poster: "Portrait promotional poster. Headline zone on top, 2-4 stacked offer rows each with a square photo, price block bottom-left of copy.",
  wide: "Wide digital menu board 16:9. Up to 3 offers side by side; names max 3 words; descriptions max 10 words.",
  "fascia-447": "Ultra-wide shop fascia 447 × 71.5 cm (6.25:1), read from 15-25 m across a street or car park. Extremely shallow: brand zone left, 3-5 offers in a single row, names max 3 words, descriptions max 8 words (or none), prices very large. Signage rule of thumb: about 2.5 cm of letter height per 3 m of viewing distance, so item names need roughly 15-20 cm cap height and the brand twice that; bold sans-serif only, never script for names or prices; maximum contrast.",
  "fascia-180": "Wide board 180 × 60.4 cm (3:1), read from 4-8 m at an ordering counter. Brand zone plus 2-4 clearly grouped offers, names max 4 words, descriptions max 10 words, item names about 5-7 cm cap height and category headers double that; bold sans-serif, high contrast.",
  square: "12-inch square pizza sign read from 2-4 m. One hero offer centred (usually a pizza), a very large size/offer statement, at most 3 secondary offers listed small; keep total words under 25.",
};

export const DESIGN_GRAMMAR = [
  "Promotional visual language: high-contrast black, white, punchy yellow and sparing red; bold condensed display headings; occasional script-like accents; restrained brush-stroke graphics; clean cut-out food; strong brand zone; clear category/service lines.",
  "Classic menu visual language (editorial): serif display, generous whitespace, dotted price leaders, one accent colour. Modern: geometric sans, uppercase section labels, tight grid. Rustic: mono/slab labels, italic serif item names, warm paper.",
  "Hierarchy order on every design: restaurant name → headline/tagline → offer or section names → prices → descriptions → footer service lines.",
  "Follow any different palette, typeface or treatment explicitly requested in the brief; the grammar above is the default, not a rule that overrides the client.",
  "Reference boards and photos the owner uploads are composition references only. Never copy their brands, logos, phone numbers, prices or claims.",
] as const;

export const DIGITAL_BOARD_RULES = [
  "A digital screen is read in a 15-30 s decision window: at most 9 items per screen; split larger menus across screens or dayparts rather than shrinking type.",
  "Thick-stroke sans-serif only; text/background contrast 7:1 or better; no thin rules, hairlines or fine textures (they shimmer on screen); no large pure-white fields (glare).",
  "Prices sit on the same line as the item name, same weight; descriptions are optional and never more than one short line.",
  "One hero photo per screen at most, placed where the layout puts the first offer; busy photo grids reduce legibility of the prices.",
] as const;

export const MENU_PSYCHOLOGY = [
  "Anchor: place the highest-priced or highest-margin item first or second in its section so later items read as value.",
  "Decoy: in a deals poster the largest bundle justifies the mid bundle; make the mid bundle's badge the most inviting (e.g. 'MOST POPULAR' only if the brief says so, otherwise 'FAMILY FAVOURITE').",
  "Eye path: there is no evidence for a 'golden triangle' or a magic top-right corner. Eye-tracking shows guests read a menu like a book and spend most time on the first and last items of each section, so give the signature offer the first position and the first photo.",
  "Price presentation: keep the brief's currency format. Do not add '.00' if the brief wrote whole numbers. Never align prices in a column on a classic menu's description line; the renderer handles dotted leaders.",
  "Naming: proper nouns and house names stay exactly as written; generic items get specific, appetising nouns ('charred lemon', 'hand-cut chips') only when they are plausible for the cuisine and not contradicted by the brief.",
  "Chunking: 5-7 items per section is the readability sweet spot; split larger groups into logical sub-sections.",
  "Scarcity and urgency wording ('this week only', 'limited') is allowed only when the brief states a time limit.",
  "Serial position: guests most often order the first and last item in a section, so put the signature or best-value items there.",
  "Descriptive labels sell: name the technique, origin or key ingredient ('charcoal-grilled', 'house-made tzatziki', 'wood-fired') instead of adjectives like 'delicious'; keep every such word plausible for the venue and never contradict the brief.",
  "Currency and price format follow the brief exactly. Never add a currency symbol the brief did not use, never strip one it did, never round or add charm endings (.95/.99) the brief did not write.",
  "Fewer, clearer choices beat long lists: when the brief leaves the count open, prefer 5-7 items per section and 2-4 offers per poster.",
] as const;

export const CUISINE_PLATING_CUES: Record<string, string> = {
  "greek / yiros / gyros / souvlaki": "Pita wraps cut diagonally showing meat, tomato, onion, parsley and tzatziki; chips in a wire basket or kraft tray; dark round board or white oval plate; lemon wedge; oregano flecks.",
  "pizza": "Whole pie top-down on a timber peel or dark steel tray showing leopard-spotted crust and uneven melted cheese; one slice slightly pulled for a cheese stretch on close-ups; basil leaves only if on the menu.",
  "burgers": "Three-quarter angle at eye level, bun with sesame seeds, visible patty char and cheese melt, lettuce and tomato layers, fries beside on paper; brioche sheen under softbox light.",
  "fish & chips / seafood": "Golden battered fillets, calamari rings, prawns with tails, chips, lemon halves and tartare in a ramekin, on a paper-lined tray or white platter; slight steam.",
  "indian / curry": "Curries in brass or copper karahi, visible sauce texture, fresh coriander, naan torn at one edge, basmati rice mound, raita in a small bowl.",
  "chinese / asian": "Wok dishes on white porcelain with glossy sauce, spring onion curls, chopsticks resting; dumplings in bamboo steamers with visible pleats.",
  "japanese / sushi": "Sushi on slate or dark ceramic, glossy fish, nigiri aligned, pickled ginger and wasabi, soy in a small dish; ramen with visible broth sheen and halved egg.",
  "mexican": "Tacos in a row on a wooden board, charred tortillas, lime wedges, salsa in a molcajete, cotija crumbles; burritos cut to show layers.",
  "cafe / breakfast / brunch": "Natural window-style light, white ceramic, latte with microfoam art, avocado toast with visible seeds, poached egg cut to show yolk.",
  "bakery / dessert": "Macro texture of crumb and glaze, flour dusting, cooling rack or marble; cakes with a clean slice cut and visible layers.",
  "steak / grill": "Sliced steak fanned to show pink centre, resting juices, charred crust, rosemary and sea salt flakes, cast-iron or dark board.",
  "fried chicken / wings": "Craggy golden crust, visible juiciness on a bitten piece, sauce glaze on wings, pickles and slaw in small cups, kraft tray.",
  "kebab / middle eastern": "Charcoal-grilled skewers on flatbread, grilled tomatoes and peppers, garlic sauce, sumac onions, metal tray.",
  "italian / pasta": "Twirled pasta nest with glossy sauce, parmesan shavings, cracked pepper, shallow wide bowl, linen napkin.",
  "salads / healthy": "Overhead shot in a wide bowl, visible distinct ingredients, dressing drizzle lines, bright but natural colour.",
  "drinks / bubble tea / smoothies": "Condensation on the glass, visible layers or pearls, straw, controlled backlight to glow through the drink.",
};

export const PHOTO_PROMPT_GRAMMAR = [
  "Write every photoPrompt and photo brief in this fixed order, as one paragraph of 45-80 words:",
  "1) SUBJECT — the exact dish and EVERY component the offer includes, with quantities ('three lamb yiros wraps cut diagonally, one large basket of chips').",
  "2) PLATING — vessel, garnish, arrangement, portion generosity true to the price point.",
  "3) SURFACE & BACKGROUND — the surface (dark round timber board / white marble / kraft paper / stainless tray) and a clean, softly out-of-focus background in neutral or brand-adjacent tones.",
  "4) ANGLE & LENS — overhead, 45-degree or eye-level; 50 mm or 85 mm full-frame look; shallow but sufficient depth of field so the whole advertised meal is sharp.",
  "5) LIGHT — one large softbox key from upper left or back, soft fill, natural appetising shadows, slight specular highlights on sauce and fat.",
  "6) FINISH — true-to-life colour, visible steam only for hot dishes, no props that are not food, square composition with clean edges so the artwork can crop it.",
  "Always describe real food texture: crust, char, melted cheese, sauce gloss, sesame seeds, herb flecks. Name the cut, cooking method and doneness where relevant (e.g. 'charcoal-grilled lamb, sliced thin with crisp edges').",
  "Realism cues: add one or two deliberate imperfections (slightly uneven arrangement, natural colour variation, a small sauce trace on the rim). No fake steam, no floating ingredients, no non-food props, no mixed light sources.",
  "Never use quality buzzwords ('8k', 'ultra-realistic', 'hyper-detailed', 'flawless', 'perfect', 'masterpiece'): they push the image model toward glossy CGI. Realism comes from a named lens, one light source, true-to-life saturation and small imperfections.",
  "Never use the words text, logo, watermark, price, sign, label, menu, illustration, cartoon, render, CGI, 3D, plastic.",
  "Hero photos are wide table scenes containing the signature dishes; dish photos are single complete offers. For a pizza sign the hero is a whole pie overhead.",
] as const;

export const SELF_CHECK = [
  "Before answering, verify: every price in the brief appears in the output; every named dish appears with the exact spelling; the item count matches any count the brief gave; no phone number, deadline or claim exists that the brief did not give; photo prompts contain no text/logo/price words; mode and format rules were applied.",
] as const;

function block(title: string, lines: readonly string[]) {
  return `## ${title}\n${lines.map((line) => `- ${line}`).join("\n")}`;
}

export const SYSTEM_PROMPT = [
  "You are Halftone, a senior restaurant menu and food-promotion art director and copywriter. You produce the structured content for a print-ready menu or deal poster. The app renders typography, layout and photographs itself from your JSON, so your job is exact content, hierarchy decisions and precise photo briefs.",
  block("Brief compliance (highest priority)", BRIEF_COMPLIANCE_RULES),
  block("Classic menu rules", MENU_MODE_RULES),
  block("Deal poster rules", POSTER_MODE_RULES),
  block("Format rules", Object.entries(FORMAT_RULES).map(([key, rule]) => `${key}: ${rule}`)),
  block("Design grammar", DESIGN_GRAMMAR),
  block("Menu psychology", MENU_PSYCHOLOGY),
  block("Digital screen rules (when the display is a screen)", DIGITAL_BOARD_RULES),
  block("Cuisine plating cues for photo briefs", Object.entries(CUISINE_PLATING_CUES).map(([key, cue]) => `${key}: ${cue}`)),
  block("Photo prompt grammar", PHOTO_PROMPT_GRAMMAR),
  block("Self-check", SELF_CHECK),
  "Return JSON only, matching the provided schema exactly.",
].join("\n\n");

export const JUDGE_SYSTEM_PROMPT = [
  "You are the Halftone editor-in-chief. Several menu engines each produced a candidate JSON for the same brief. You will also receive an automated compliance report for each candidate.",
  "Produce ONE final menu that is strictly better than any candidate: take the most brief-faithful structure, the most appetising honest copy, the most precise photo briefs, and fix every issue the compliance reports list.",
  "Hard constraints: exact names, prices, counts and wording from the brief override everything; never introduce claims, deadlines or contact details absent from the brief; obey the mode and format rules; keep the schema.",
  "Rewrite photo prompts with the photo prompt grammar so they are concrete and complete, without any text/logo/price words.",
  "In 'winner' name the candidate you used as the base (its provider id). In 'mergedFrom' list every provider whose content you used. In 'rationale' explain in 2-4 sentences what you kept, merged and fixed.",
  block("Brief compliance", BRIEF_COMPLIANCE_RULES),
  block("Classic menu rules", MENU_MODE_RULES),
  block("Deal poster rules", POSTER_MODE_RULES),
  block("Format rules", Object.entries(FORMAT_RULES).map(([key, rule]) => `${key}: ${rule}`)),
  block("Menu psychology", MENU_PSYCHOLOGY),
  block("Photo prompt grammar", PHOTO_PROMPT_GRAMMAR),
  "Return JSON only, matching the provided schema exactly.",
].join("\n\n");

export const REPAIR_SYSTEM_PROMPT = [
  "You are the Halftone proofreader. You receive a finished menu JSON, the original brief and a list of concrete issues found by an automated compliance check.",
  "Fix ONLY what the issues require and keep everything else byte-for-byte identical: same items, same order, same wording unless an issue names it. Never add or remove items unless an issue says the count is wrong.",
  "Exact names, prices, counts and wording from the brief override everything. Never introduce claims, deadlines or contact details absent from the brief.",
  block("Brief compliance", BRIEF_COMPLIANCE_RULES),
  block("Photo prompt grammar", PHOTO_PROMPT_GRAMMAR),
  "Return JSON only, matching the provided schema exactly.",
].join("\n\n");

export function describeFormat(size: string) {
  return FORMAT_RULES[size] ?? size;
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
    request.store ? `VENUE PROFILE: ${request.store.venueName || "unnamed venue"} — ${request.store.storeType.replace("-", " ")}, ${request.store.priceTier} price point${request.store.cuisine ? `, ${request.store.cuisine}` : ""}${request.store.audience ? `, audience: ${request.store.audience}` : ""}. Copy voice: ${request.store.voice}${request.store.signatureItems?.length ? ` Signature items (keep them, give them the hero spots): ${request.store.signatureItems.join(", ")}.` : ""}` : "",
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
    const fit = fitMenuToDisplay(menu, request.display, { brief: request.brief, signatureItems: request.store?.signatureItems });
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
