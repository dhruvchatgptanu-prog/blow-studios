import type { EngineReport, GeneratedMenuCore } from "./schema";

/**
 * Chat intent parsing for the conversational studio. Deterministic, no model
 * call: it decides whether a message starts a new design, revises the current
 * one, asks for photos or is just a question, and which mode / format / style
 * the words imply. Anything it cannot decide keeps the conversation's settings.
 */

export type ChatMode = "menu" | "poster";
export type ChatSize = "letter" | "a5" | "poster" | "wide" | "fascia-447" | "fascia-180" | "square";
export type ChatStyle = "editorial" | "modern" | "rustic" | "bold-deals";
export type ChatPages = "1" | "2";

export type IntentContext = { hasMenu: boolean; mode: ChatMode; size: ChatSize; pages: ChatPages; style: ChatStyle };

export type Intent = {
  kind: "new" | "revise" | "photos" | "help";
  mode: ChatMode;
  size: ChatSize;
  pages: ChatPages;
  style: ChatStyle;
  /** The brief text the engines receive (the message itself for a new design). */
  brief: string;
  /** Why the parser decided what it did; shown nowhere, useful in tests. */
  reason: string;
};

const NEW_WORDS = /\b(?:create|make|design|build|generate|draft|do|start|give me|i need|i want|can you (?:make|create|design|do))\b/i;
const ARTEFACT_WORDS = /\b(?:menu|menus|poster|board|sign|signage|fascia|flyer|specials?|deals?|promo|promotion|price list|pricelist)\b/i;
const RESTART_WORDS = /\b(?:new (?:menu|poster|board|sign|design|one)|start (?:over|again|fresh)|from scratch|another (?:menu|poster|board|design)|different (?:venue|restaurant|shop))\b/i;
const REVISE_WORDS = /\b(?:change|swap|replace|rename|remove|delete|drop|add|put|move|make (?:it|the|them|this|that)|bigger|smaller|larger|shorter|longer|bolder|less|more|instead|rather|fix|correct|update|tweak|adjust|try|use|keep|only|without|also|and (?:also )?add|can you (?:change|make it|fix|swap|add|remove))\b/i;
const PHOTO_WORDS = /^(?:\s*(?:please|now|ok|okay|great|yes|and)[\s,]*)*(?:create|make|generate|shoot|take|do|render|add)\s+(?:the\s+|some\s+|all\s+(?:the\s+)?)?(?:food\s+)?(?:photos?|pictures?|images?|photography|shots?)\b|^\s*photos?\s*(?:please)?\s*[.!]?\s*$/i;
const HELP_WORDS = /^\s*(?:hi|hello|hey|yo|help|what can you do|what do you do|how does this work|how do i|\?+)\s*[.!?]*\s*$/i;

function sizeFrom(text: string, mode: ChatMode, current: ChatSize, changedMode: boolean): ChatSize {
  if (/\b447\b|\bfascia\b.*\b4\.?47?\s?m\b|\blong fascia\b/i.test(text)) return "fascia-447";
  if (/\bfascia\b|\b180\s?(?:x|×)\s?60\b|\bshop ?front board\b/i.test(text)) return "fascia-180";
  if (/\b(?:square|pizza sign|12[- ]?inch|12")\b/i.test(text)) return "square";
  if (/\b(?:wide|landscape|tv|screen|digital board|menu board|wide board)\b/i.test(text)) return mode === "poster" ? "wide" : "letter";
  if (/\ba5\b/i.test(text)) return "a5";
  if (/\b(?:letter|a4|one[- ]page|single page|two[- ]page|2[- ]page)\b/i.test(text)) return "letter";
  if (/\b(?:portrait|poster)\b/i.test(text) && mode === "poster") return "poster";
  if (changedMode) return mode === "poster" ? "poster" : "letter";
  // Keep the current size only if it belongs to the mode.
  const posterSizes: ChatSize[] = ["poster", "wide", "fascia-447", "fascia-180", "square"];
  const belongs = mode === "poster" ? posterSizes.includes(current) : current === "letter" || current === "a5";
  return belongs ? current : mode === "poster" ? "poster" : "letter";
}

function modeFrom(text: string, current: ChatMode): ChatMode {
  const poster = /\b(?:poster|deal|deals|board|sign|signage|fascia|specials?|promo|promotion|offer|offers|combo|combos|bundle|bundles|flyer)\b/i.test(text);
  const menu = /\b(?:menu|menus|price list|pricelist|a5|letter|a4|table menu|takeaway menu|dine[- ]in)\b/i.test(text);
  if (poster && !menu) return "poster";
  if (menu && !poster) return "menu";
  if (poster && menu) return /\bmenu board\b/i.test(text) ? "poster" : "menu";
  return current;
}

function styleFrom(text: string, mode: ChatMode, current: ChatStyle): ChatStyle {
  if (mode === "poster") return "bold-deals";
  if (/\b(?:editorial|classic|elegant|fine|serif|refined)\b/i.test(text)) return "editorial";
  if (/\b(?:modern|minimal|clean|geometric|sans)\b/i.test(text)) return "modern";
  if (/\b(?:rustic|warm|kraft|farmhouse|handmade|chalk)\b/i.test(text)) return "rustic";
  return current === "bold-deals" ? "editorial" : current;
}

function pagesFrom(text: string, current: ChatPages): ChatPages {
  if (/\b(?:two[- ]pages?|2[- ]pages?|double[- ]sided|front and back)\b/i.test(text)) return "2";
  if (/\b(?:one[- ]page|1[- ]page|single[- ]page|single[- ]sided)\b/i.test(text)) return "1";
  return current;
}

export function parseIntent(message: string, context: IntentContext): Intent {
  const text = message.trim();
  const base = { pages: pagesFrom(text, context.pages), brief: text };
  if (!text || HELP_WORDS.test(text)) return { kind: "help", mode: context.mode, size: context.size, style: context.style, ...base, reason: "greeting or question" };
  if (context.hasMenu && PHOTO_WORDS.test(text)) return { kind: "photos", mode: context.mode, size: context.size, style: context.style, ...base, reason: "asks for photos" };

  const mode = modeFrom(text, context.mode);
  const changedMode = mode !== context.mode;
  const size = sizeFrom(text, mode, context.size, changedMode);
  const style = styleFrom(text, mode, context.style);
  const describesContent = /[$€£]\s?\d|\d+(?:[.,]\d{2})\b|\bfor\s+[A-Z][\w'&]+/.test(text) || text.length > 140;

  if (!context.hasMenu) return { kind: "new", mode, size, style, ...base, reason: "no design yet" };
  if (RESTART_WORDS.test(text)) return { kind: "new", mode, size, style, ...base, reason: "asks to start over" };
  if (NEW_WORDS.test(text) && ARTEFACT_WORDS.test(text) && (describesContent || changedMode)) return { kind: "new", mode, size, style, ...base, reason: "describes a new artefact" };
  if (REVISE_WORDS.test(text) || text.length < 140) return { kind: "revise", mode, size, style, ...base, reason: "edit instruction on the current design" };
  return { kind: "new", mode, size, style, ...base, reason: "long message without edit words" };
}

/** Folds a revision request into the running brief so the verifier still enforces the original exact names and prices. */
export function extendBrief(brief: string, revision: string) {
  const trimmed = revision.trim();
  if (!trimmed) return brief;
  return `${brief.trim()}\n\nREVISION: ${trimmed}`;
}

/** The assistant's reply text for a finished design. Deterministic; no credits. */
export function describeResult(menu: GeneratedMenuCore & { engine?: EngineReport }, intent: Pick<Intent, "kind" | "size">): string {
  const items = menu.sections.flatMap((section) => section.items);
  const what = menu.mode === "poster" ? "poster" : "menu";
  const sizeLabel: Record<ChatSize, string> = { letter: "Letter page", a5: "A5 page", poster: "portrait poster", wide: "wide board", "fascia-447": "447 × 71.5 cm fascia", "fascia-180": "180 × 60.4 cm board", square: "12-inch square sign" };
  const lines: string[] = [];
  lines.push(`${intent.kind === "revise" ? "Updated" : "Here is"} the ${what} for ${menu.restaurantName || "your venue"}: ${items.length} ${menu.mode === "poster" ? "offers" : "items"} across ${menu.sections.length} section${menu.sections.length === 1 ? "" : "s"} on a ${sizeLabel[intent.size]}.`);
  const engine = menu.engine;
  if (engine) {
    const ok = engine.candidates.filter((candidate) => candidate.status === "ok");
    const engines = ok.map((candidate) => candidate.provider === "lovable" ? "Halftone" : candidate.provider === "openai" ? "ChatGPT" : "Claude");
    lines.push(`Checked against your words: ${engine.finalScore}/100${engines.length > 1 ? ` (best of ${engines.join(", ")}${engine.judge ? `, merged by ${engine.judge.provider === "openai" ? "ChatGPT" : engine.judge.provider === "claude" ? "Claude" : "Halftone"}` : ""})` : engines.length === 1 ? ` (${engines[0]})` : ""}.`);
    if (engine.finalIssues.length) lines.push(`Still check: ${engine.finalIssues.slice(0, 3).join(" ")}`);
    if (engine.fit?.omitted.length) lines.push(`Left out so it stays legible on the display: ${engine.fit.omitted.join(", ")}.`);
  }
  if (menu.followed.length) lines.push(`Followed: ${menu.followed.slice(0, 4).join("; ")}.`);
  lines.push("Tell me what to change, or say \"create the photos\".");
  return lines.join("\n");
}

export const HELP_REPLY = [
  "I design print-ready menus and deal posters from plain words.",
  "Tell me the venue, the dishes or offers and the exact prices, for example: \"Create a bold family deals poster for Yiros House: Yiros Meal Deal — 3 lamb yiros and large chips $49.90 …\".",
  "Then just talk: \"make the prices bigger\", \"swap the second offer for a kids meal $12\", \"turn it into a wide board\", \"create the photos\".",
  "Attach a photo of your current menu to redesign it, or a food photo to improve it. Say \"set up my store\" for templates matched to your venue and a design fitted to your real display.",
].join("\n");

export function conversationTitle(text: string) {
  const venue = /\b(?:for|at)\s+([A-Z][\w'&]*(?:\s+[A-Z][\w'&]*){0,3})/.exec(text)?.[1];
  const base = venue ?? text.replace(/\s+/g, " ").trim();
  return base.length > 42 ? `${base.slice(0, 41).trimEnd()}…` : base || "New design";
}

export const STARTERS = [
  { title: "Family deals poster", text: "Create a bold family deals poster for Yiros House. Include: Yiros Meal Deal — 3 lamb yiros and large chips $49.90; Seafood Family Pack — 3 fish, calamari, prawns and chips $59.90; Big Family Yiros — 5 chicken yiros and family chips $69.90. Use the exact names and prices." },
  { title: "Classic one-page menu", text: "Create a one-page menu for Yiros House with sections for yiros, grill plates and sides. Keep the writing concise and use dollar prices." },
  { title: "Shop-front fascia", text: "Fascia sign 447 × 71.5 cm for Istanbul Kebabs: Chicken kebab $14, Lamb kebab $15, HSP $16, Falafel wrap $12. Halal." },
  { title: "Pizza window sign", text: "12-inch pizza sign for Luigi's Pizzeria. Margherita $14, Pepperoni $18, Family Deal — 2 large pizzas and garlic bread $39." },
] as const;
