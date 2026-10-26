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
  checks.structure = Math.max(0, structure);

  const score = Math.max(0, Math.min(100, Object.values(checks).reduce<number>((sum, value) => sum + (value ?? 0), 0)));
  return { score, issues: Array.from(new Set(issues)), checks: checks as Record<string, number> };
}
