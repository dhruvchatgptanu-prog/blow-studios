import { themeContrast } from "./color";
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
    theme: { "--poster": "oklch(0.5 0.19 38)", "--sun": "oklch(0.78 0.15 70)", "--paper": "oklch(0.2 0.01 40)", "--card": "oklch(0.26 0.015 45)", "--ink": "oklch(0.95 0.02 80)", "--poster-foreground": "oklch(0.98 0.01 90)", "--press": "oklch(0.8 0.16 60)", "--press-deep": "oklch(0.86 0.14 70)", "--muted": "oklch(0.78 0.02 70)", "--line": "oklch(0.35 0.02 45)" },
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
    theme: { "--paper": "oklch(0.3 0.01 250)", "--card": "oklch(0.34 0.012 250)", "--ink": "oklch(0.96 0.005 90)", "--press": "oklch(0.78 0.17 35)", "--press-deep": "oklch(0.88 0.1 75)", "--poster": "oklch(0.5 0.2 30)", "--line": "oklch(0.45 0.01 250)", "--muted": "oklch(0.8 0.01 250)" },
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
    theme: { "--poster": "oklch(0.5 0.22 350)", "--sun": "oklch(0.86 0.16 110)", "--herb": "oklch(0.5 0.12 190)", "--ink": "oklch(0.22 0.03 300)" },
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
    theme: { "--paper": "oklch(0.15 0.01 260)", "--card": "oklch(0.2 0.012 260)", "--ink": "oklch(0.97 0.005 90)", "--poster": "oklch(0.75 0.17 60)", "--poster-foreground": "oklch(0.15 0.01 260)", "--sun": "oklch(0.75 0.17 60)", "--press": "oklch(0.82 0.16 65)", "--press-deep": "oklch(0.86 0.15 70)", "--line": "oklch(0.3 0.01 260)", "--muted": "oklch(0.75 0.01 260)" },
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
    // Legibility: distance-read surfaces and dim rooms need enhanced (7:1) contrast; never recommend a failing theme.
    const contrast = themeContrast(template.theme);
    const distanceRead = profile.displaySurfaces.some((surface) => ["fascia", "wall-board", "digital-screen", "drive-thru-board", "window", "a-frame"].includes(surface));
    if (contrast.level === "fail") score -= 40;
    else if (contrast.level === "AAA") { score += distanceRead || profile.lighting === "dim" ? 8 : 3; reasons.push(`${contrast.weakest}:1 text contrast (AAA)`); }
    else if (distanceRead || profile.lighting === "dim") score -= 6;
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
