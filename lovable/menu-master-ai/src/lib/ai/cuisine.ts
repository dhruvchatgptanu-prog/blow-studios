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

const REALISM = "Natural imperfections: slightly uneven arrangement, natural colour variation, a small sauce or crumb trace on the rim; true-to-life saturation and natural highlights. No fake steam, no non-food props, no text, no logo, no watermark, no illustration, no CGI, no plastic-looking food, no waxy sheen.";

/** Quality buzzwords ("8k", "hyper-detailed", "flawless") push image models toward glossy CGI; they are removed, never added. */
const BUZZWORDS = /\b(?:8k|4k|uhd|ultra[- ]?realistic|hyper[- ]?realistic|hyper[- ]?detailed|photorealistic render|octane(?: render)?|unreal engine|flawless|perfect(?:ly)?|award[- ]winning photo(?:graph)?|masterpiece|trending on artstation)\b[,;]?\s*/gi;

function has(prompt: string, re: RegExp) {
  return re.test(prompt);
}

/** Ensures a prompt carries surface, angle, light, finish and realism cues without duplicating what the model already wrote. Idempotent. */
export function enrichPhotoPrompt(prompt: string, cuisines: string[], shape: "square" | "wide" = "square"): string {
  const defaults = DEFAULTS[cuisines[0] ?? ""] ?? GENERIC;
  const parts = [prompt.replace(BUZZWORDS, "").replace(/\s+([,.;])/g, "$1").replace(/\s+/g, " ").trim()];
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
