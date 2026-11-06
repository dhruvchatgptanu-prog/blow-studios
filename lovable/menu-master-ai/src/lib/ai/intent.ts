import { displayFromWords, rendererSizeForDisplay, type DisplaySpec } from "./display";
import type { EngineReport, GeneratedMenuCore } from "./schema";
import { profileFromText } from "./store";
import { recommendTemplates, TEMPLATES, templateById, type Template } from "./templates";

/**
 * Chat intent parsing for the conversational studio. Deterministic, no model
 * call: it decides whether a message starts a new design, revises the current
 * one, asks for photos, asks for another look or is just a question; reads the
 * mode, physical size, style and page count from the words; and picks the
 * template that suits the venue and cuisine. Anything it cannot decide keeps
 * the conversation's settings.
 */

export type ChatMode = "menu" | "poster";
export type ChatSize = "letter" | "a5" | "poster" | "wide" | "fascia-447" | "fascia-180" | "square";
export type ChatStyle = "editorial" | "modern" | "rustic" | "bold-deals";
export type ChatPages = "1" | "2";

/** The part of a template the chat keeps with a design (theme = CSS variables for the sheet). */
export type LookRef = { id: string; name: string; style: ChatStyle; theme: Record<string, string>; alternatives: string[] };

export type IntentContext = {
  hasMenu: boolean;
  mode: ChatMode;
  size: ChatSize;
  pages: ChatPages;
  style: ChatStyle;
  /** Size the conversation already works to (from earlier words or the store wizard). */
  display?: DisplaySpec | undefined;
  /** Look the conversation already uses. */
  look?: LookRef | undefined;
  /** A template chosen in the store wizard wins unless the words name another. */
  setupTemplateId?: string | undefined;
};

export type Intent = {
  kind: "new" | "revise" | "photos" | "restyle" | "help";
  mode: ChatMode;
  size: ChatSize;
  pages: ChatPages;
  style: ChatStyle;
  display: DisplaySpec | undefined;
  look: LookRef | undefined;
  /** The brief text the engines receive (the message itself for a new design). */
  brief: string;
  /** The message asks for photography; the chat creates the photos right after the design. */
  wantsPhotos: boolean;
  /** Why the parser decided what it did; shown nowhere, useful in tests. */
  reason: string;
};

const NEW_WORDS = /\b(?:create|make|design|build|generate|draft|do|start|give me|i need|i want|can you (?:make|create|design|do))\b/i;
const ARTEFACT_WORDS = /\b(?:menu|menus|poster|board|sign|signage|fascia|flyer|specials?|deals?|promo|promotion|price list|pricelist)\b/i;
const RESTART_WORDS = /\b(?:new (?:menu|poster|board|sign|design|one)|start (?:over|again|fresh)|from scratch|another (?:menu|poster|board|design)|different (?:venue|restaurant|shop))\b/i;
const REVISE_WORDS = /\b(?:change|swap|replace|rename|remove|delete|drop|add|put|move|make (?:it|the|them|this|that)|bigger|smaller|larger|shorter|longer|bolder|less|more|instead|rather|fix|correct|update|tweak|adjust|try|use|keep|only|without|also|and (?:also )?add|can you (?:change|make it|fix|swap|add|remove))\b/i;
const PHOTO_WORDS = /^(?:\s*(?:please|now|ok|okay|great|yes|and)[\s,]*)*(?:create|make|generate|shoot|take|do|render|add)\s+(?:the\s+|some\s+|all\s+(?:the\s+)?)?(?:food\s+)?(?:photos?|pictures?|images?|photography|shots?)\b|^\s*photos?\s*(?:please)?\s*[.!]?\s*$/i;
const RESTYLE_WORDS = /^(?:\s*(?:please|now|ok|okay|and|hmm)[\s,]*)*(?:(?:try|show|give me|show me|i want|let's see|lets see|do|make it)\s+)?(?:a\s+|the\s+)?(?:more\s+)?(?:another|different|new|next|unique|fresh|other|bolder|cooler|nicer|better)\s+(?:look|style|design|template|theme|colou?rs?|vibe|feel|layout)\b|^\s*(?:restyle|change the (?:look|style|theme|colou?rs?|design)|make it look different|different (?:look|style|theme))\s*[.!]?\s*$/i;
/** The message asks for photography as part of the design ("include pictures", "with photos of each dish"). */
const WANTS_PHOTOS = /\b(?:include|with|add|put|show|insert|want|need|generate|create|make)\s+(?:the\s+|some\s+|more\s+|real\s+|food\s+|nice\s+|good\s+)*(?:pictures?|photos?|images?|photography|pics)\b|\b(?:pictures?|photos?|images?)\s+(?:of|for)\s+(?:each|every|the|all)\b/i;
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
  const posterSizes: ChatSize[] = ["poster", "wide", "fascia-447", "fascia-180", "square"];
  const belongs = mode === "poster" ? posterSizes.includes(current) : current === "letter" || current === "a5";
  return belongs ? current : mode === "poster" ? "poster" : "letter";
}

function modeFrom(text: string, current: ChatMode, display: DisplaySpec | undefined): { mode: ChatMode; explicit: boolean } {
  const poster = /\b(?:poster|deal|deals|board|sign|signage|fascia|specials?|promo|promotion|offer|offers|combo|combos|bundle|bundles|flyer)\b/i.test(text);
  const menu = /\b(?:menu|menus|price list|pricelist|a5|letter|a4|table menu|takeaway menu|dine[- ]in)\b/i.test(text);
  if (poster && !menu) return { mode: "poster", explicit: true };
  if (menu && !poster) return { mode: "menu", explicit: true };
  if (poster && menu) return { mode: /\bmenu board\b/i.test(text) ? "poster" : "menu", explicit: true };
  if (display) {
    if (display.displayType === "table-menu" || display.displayType === "counter-card") return { mode: "menu", explicit: false };
    if (display.displayType !== "unknown") return { mode: "poster", explicit: false };
  }
  return { mode: current, explicit: false };
}

function styleWords(text: string): ChatStyle | undefined {
  if (/\b(?:editorial|classic|elegant|fine|serif|refined)\b/i.test(text)) return "editorial";
  if (/\b(?:modern|minimal|clean|geometric|sans)\b/i.test(text)) return "modern";
  if (/\b(?:rustic|warm|kraft|farmhouse|handmade|chalk)\b/i.test(text)) return "rustic";
  return undefined;
}

function pagesFrom(text: string, current: ChatPages): ChatPages {
  if (/\b(?:two[- ]pages?|2[- ]pages?|double[- ]sided|front and back)\b/i.test(text)) return "2";
  if (/\b(?:one[- ]page|1[- ]page|single[- ]page|single[- ]sided)\b/i.test(text)) return "1";
  return current;
}

function lookOf(template: Template, alternatives: Template[]): LookRef {
  return { id: template.id, name: template.name, style: template.style, theme: { ...(template.theme as Record<string, string>) }, alternatives: alternatives.filter((other) => other.id !== template.id).map((other) => other.id) };
}

/** A template named in the words ("noir", "chalkboard pub", "kraft cafe look"), if any. */
export function templateFromWords(text: string): Template | undefined {
  const lower = text.toLowerCase();
  return TEMPLATES.find((template) => {
    const name = template.name.toLowerCase();
    return lower.includes(name) || lower.includes(template.id.replace("-", " ")) || name.split(/\s+/).every((word) => word.length > 3 && new RegExp(`\\b${word}\\b`).test(lower));
  });
}

/**
 * Chooses the look for a design from the venue and cuisine in the words: every
 * restaurant type gets its own template (colours, typography, layout grammar).
 * The words win, then the store wizard's choice, then the best-scoring match.
 */
export function chooseLook(text: string, mode: ChatMode, context: Pick<IntentContext, "setupTemplateId" | "look">, forceNew = false): LookRef {
  const profile = profileFromText(text);
  const ranked = recommendTemplates(profile, 16).map((match) => match.template);
  const sameMode = ranked.filter((template) => template.mode === mode);
  const pool = sameMode.length ? sameMode : ranked;
  const named = templateFromWords(text);
  if (named) return lookOf(named, pool);
  const fromSetup = templateById(context.setupTemplateId);
  if (fromSetup && !forceNew) return lookOf(fromSetup, pool);
  if (context.look && !forceNew && templateById(context.look.id)?.mode === mode) return { ...context.look, alternatives: pool.filter((template) => template.id !== context.look?.id).map((template) => template.id) };
  const first = pool[0] ?? TEMPLATES[0]!;
  return lookOf(first, pool);
}

/** The next look in the ranked alternatives, cycling. */
export function nextLook(current: LookRef): LookRef {
  const ids = [...current.alternatives, current.id];
  const next = templateById(ids[0]) ?? TEMPLATES[0]!;
  const rest = ids.slice(1);
  return { id: next.id, name: next.name, style: next.style, theme: { ...(next.theme as Record<string, string>) }, alternatives: rest };
}

export function parseIntent(message: string, context: IntentContext): Intent {
  const text = message.trim();
  const pages = pagesFrom(text, context.pages);
  const wantsPhotos = WANTS_PHOTOS.test(text);
  const keep = { mode: context.mode, size: context.size, style: context.style, display: context.display, look: context.look, pages, brief: text, wantsPhotos };
  if (!text || HELP_WORDS.test(text)) return { kind: "help", ...keep, reason: "greeting or question" };
  if (context.hasMenu && PHOTO_WORDS.test(text)) return { kind: "photos", ...keep, reason: "asks for photos" };
  if (context.hasMenu && (RESTYLE_WORDS.test(text) || (templateFromWords(text) && text.length < 80 && !NEW_WORDS.test(text)))) {
    const named = templateFromWords(text);
    const look = named ? chooseLook(text, context.mode, context) : context.look ? nextLook(context.look) : chooseLook(text, context.mode, context, true);
    return { kind: "restyle", ...keep, look, style: look.style, reason: named ? "names a template" : "asks for another look" };
  }

  const spokenDisplay = displayFromWords(text);
  const display = spokenDisplay ?? context.display;
  const { mode, explicit } = modeFrom(text, context.mode, spokenDisplay ?? undefined);
  const changedMode = mode !== context.mode;
  const size = display ? rendererSizeForDisplay(display, mode) : sizeFrom(text, mode, context.size, changedMode);
  const describesContent = /[$€£]\s?\d|\d+(?:[.,]\d{2})\b|\bfor\s+[A-Z][\w'&]+/.test(text) || text.length > 140;

  let kind: Intent["kind"];
  let reason: string;
  if (!context.hasMenu) { kind = "new"; reason = "no design yet"; }
  else if (RESTART_WORDS.test(text)) { kind = "new"; reason = "asks to start over"; }
  else if (NEW_WORDS.test(text) && ARTEFACT_WORDS.test(text) && (describesContent || (changedMode && explicit))) { kind = "new"; reason = "describes a new artefact"; }
  else if (REVISE_WORDS.test(text) || text.length < 140) { kind = "revise"; reason = "edit instruction on the current design"; }
  else { kind = "new"; reason = "long message without edit words"; }

  const look = chooseLook(text, mode, context, kind === "new" && RESTART_WORDS.test(text));
  const explicitStyle = styleWords(text);
  const style: ChatStyle = mode === "poster" ? "bold-deals" : explicitStyle ?? (look.style === "bold-deals" ? "editorial" : look.style);
  return { kind, mode, size, pages, style, display, look, brief: text, wantsPhotos, reason };
}

/** Folds a revision request into the running brief so the verifier still enforces the original exact names and prices. */
export function extendBrief(brief: string, revision: string) {
  const trimmed = revision.trim();
  if (!trimmed) return brief;
  return `${brief.trim()}\n\nREVISION: ${trimmed}`;
}

const SIZE_LABEL: Record<ChatSize, string> = { letter: "Letter page", a5: "A5 page", poster: "portrait poster", wide: "wide board", "fascia-447": "447 × 71.5 cm fascia", "fascia-180": "180 × 60.4 cm board", square: "12-inch square sign" };

export function describeDisplay(display: DisplaySpec) {
  const type = display.displayType === "unknown" ? "display" : display.displayType.replace("-", " ");
  return `${display.widthCm} × ${display.heightCm} cm ${type}, read from about ${display.viewingDistanceM} m (fits up to ${display.budget.maxItems} items or ${display.budget.maxOffers} photo offers legibly)`;
}

/** The assistant's reply text for a finished design. Deterministic; no credits. */
export function describeResult(menu: GeneratedMenuCore & { engine?: EngineReport }, intent: Pick<Intent, "kind" | "size"> & { display?: DisplaySpec | undefined; look?: LookRef | undefined; wantsPhotos?: boolean | undefined }): string {
  const items = menu.sections.flatMap((section) => section.items);
  const what = menu.mode === "poster" ? "poster" : "menu";
  const lines: string[] = [];
  lines.push(`${intent.kind === "revise" ? "Updated" : "Here is"} the ${what} for ${menu.restaurantName || "your venue"}: ${items.length} ${menu.mode === "poster" ? "offers" : "items"} across ${menu.sections.length} section${menu.sections.length === 1 ? "" : "s"}${intent.display ? `, fitted to your ${describeDisplay(intent.display)}` : ` on a ${SIZE_LABEL[intent.size]}`}.`);
  if (intent.look) lines.push(`Look: ${intent.look.name}. Say "another look" or name a template to change it without re-generating.`);
  const engine = menu.engine;
  if (engine) {
    const ok = engine.candidates.filter((candidate) => candidate.status === "ok");
    const engineName = (id: string) => (id === "lovable" ? "Halftone" : id === "openai" ? "ChatGPT" : id === "gemini" ? "Gemini" : "Claude");
    const engines = ok.map((candidate) => engineName(candidate.provider));
    lines.push(`Checked against your words: ${engine.finalScore}/100${engines.length > 1 ? ` (best of ${engines.join(", ")}${engine.judge ? `, merged by ${engineName(engine.judge.provider)}` : ""})` : engines.length === 1 ? ` (${engines[0]})` : ""}.`);
    if (engine.finalIssues.length) lines.push(`Still check: ${engine.finalIssues.slice(0, 3).join(" ")}`);
    if (engine.fit?.omitted.length) lines.push(`Left out so it stays legible at that size: ${engine.fit.omitted.join(", ")}.`);
  }
  if (!intent.display) lines.push("Tell me the real size any time (\"120 × 60 cm board\", \"A4 landscape\", \"55-inch TV\") and I will refit it.");
  if (menu.followed.length) lines.push(`Followed: ${menu.followed.slice(0, 4).join("; ")}.`);
  lines.push(intent.wantsPhotos ? "Creating the photos now." : "Tell me what to change, or say \"create the photos\".");
  return lines.join("\n");
}

export const HELP_REPLY = [
  "I design print-ready menus and deal posters from plain words.",
  "Tell me the venue, the dishes or offers and the exact prices, for example: \"Create a bold family deals poster for Yiros House: Yiros Meal Deal — 3 lamb yiros and large chips $49.90 …\".",
  "Add the real size in the same message (\"120 × 60 cm board above the counter\", \"A4 landscape\", \"55-inch TV\") and I fit the design to it. Each type of venue gets its own look; say \"another look\" to cycle.",
  "Then just talk: \"make the prices bigger\", \"swap the second offer for a kids meal $12\", \"create the photos\". Attach photos of your current menu (all pages) to redesign it, or a food photo to improve it.",
].join("\n");

export function conversationTitle(text: string) {
  const venue = /\b(?:for|at)\s+([A-Z][\w'&]*(?:\s+[A-Z][\w'&]*){0,3})/.exec(text)?.[1];
  const base = venue ?? text.replace(/\s+/g, " ").trim();
  return base.length > 42 ? `${base.slice(0, 41).trimEnd()}…` : base || "New design";
}

export const STARTERS = [
  { title: "Family deals board, 120 × 60 cm", text: "Create a bold family deals board 120 x 60 cm above the counter for Yiros House. Include: Yiros Meal Deal — 3 lamb yiros and large chips $49.90; Seafood Family Pack — 3 fish, calamari, prawns and chips $59.90; Big Family Yiros — 5 chicken yiros and family chips $69.90. Use the exact names and prices." },
  { title: "A4 café menu", text: "Create an A4 one-page menu for Nook Café with sections for coffee, breakfast and toasties. Flat white $5, Big Breakfast $24, Ham & Cheese Toastie $12. Keep it to 12 items." },
  { title: "Shop-front fascia", text: "Fascia sign 447 × 71.5 cm for Istanbul Kebabs, read from the street: Chicken kebab $14, Lamb kebab $15, HSP $16, Falafel wrap $12. Halal." },
  { title: "55-inch screen for sushi", text: "Menu for a 55-inch TV screen behind the counter at Umi Sushi: Salmon Nigiri (2) $8, Tuna Roll $12, Chicken Katsu Don $18, Miso Soup $4, Green Tea $3." },
] as const;
