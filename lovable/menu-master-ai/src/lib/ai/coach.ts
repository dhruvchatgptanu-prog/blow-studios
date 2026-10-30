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
  if (/\((?:V|VG|VE|GF|DF|NF|H|K)\)/.test(text) && !/\b(?:V|VG|GF|DF|NF|H)\s*=\s*|\bkey\b|\blegend\b/i.test(text)) hints.push({ level: "tip", text: "Dietary markers found. They will be kept exactly as written; add a key line (e.g. 'V = vegetarian, GF = gluten free') if you want one printed." });
  if (!hasPhone) hints.push({ level: "tip", text: "No phone number given, so none will be printed. Add one if you want it on the design." });
  if (!hasNegatives && text.length < 160) hints.push({ level: "tip", text: "Mention anything to avoid (e.g. 'no descriptions', 'no red') and any colours or fonts you want." });

  return { score: Math.max(0, score), hints: hints.slice(0, 4) };
}
