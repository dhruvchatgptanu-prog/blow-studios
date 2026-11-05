import { parseIntent, extendBrief, describeResult, conversationTitle, HELP_REPLY } from "./out/src/lib/ai/intent.js";

let f = 0; const t = (l, c, d = "") => { if (!c) { f++; console.log("FAIL", l, d); } else console.log("PASS", l); };
const fresh = { hasMenu: false, mode: "poster", size: "poster", pages: "1", style: "bold-deals" };
const withPoster = { ...fresh, hasMenu: true };
const withMenu = { hasMenu: true, mode: "menu", size: "letter", pages: "1", style: "editorial" };

const a = parseIntent("Create a bold family deals poster for Yiros House. Yiros Meal Deal — 3 lamb yiros and large chips $49.90.", fresh);
t("first message is a new poster", a.kind === "new" && a.mode === "poster" && a.size === "poster" && a.style === "bold-deals", JSON.stringify(a));
const b = parseIntent("Create a one-page menu for Yiros House with sections for yiros, grill plates and sides. Use dollar prices.", fresh);
t("menu words switch to menu mode on letter", b.kind === "new" && b.mode === "menu" && b.size === "letter" && b.style === "editorial", JSON.stringify(b));
const c = parseIntent("make the prices bigger and swap the second offer for a Kids Meal $12", withPoster);
t("edit instruction revises", c.kind === "revise" && c.mode === "poster" && c.size === "poster", JSON.stringify(c));
const d = parseIntent("turn it into a wide board", withPoster);
t("format change stays a revision with new size", d.kind === "revise" && d.size === "wide", JSON.stringify(d));
const e = parseIntent("create the photos", withPoster);
t("photo request", e.kind === "photos", JSON.stringify(e));
t("photos ignored without a design", parseIntent("create the photos", fresh).kind === "new");
const g = parseIntent("hi", fresh);
t("greeting is help", g.kind === "help" && HELP_REPLY.includes("exact prices"));
const h = parseIntent("Now do a new menu for Luigi's Pizzeria: Margherita $14, Pepperoni $18.", withPoster);
t("new venue starts over", h.kind === "new" && h.mode === "menu", JSON.stringify(h));
const i = parseIntent("Fascia sign 447 × 71.5 cm for Istanbul Kebabs: Chicken kebab $14, Lamb kebab $15.", fresh);
t("fascia 447 detected", i.size === "fascia-447" && i.mode === "poster", JSON.stringify(i));
const j = parseIntent("12-inch pizza sign for Luigi's. Margherita $14.", fresh);
t("square sign detected", j.size === "square", JSON.stringify(j));
const k = parseIntent("make it two pages and rustic", withMenu);
t("pages and style from words", k.kind === "revise" && k.pages === "2" && k.style === "rustic", JSON.stringify(k));
const l = parseIntent("A5 menu for Nook Café with coffee, breakfast and toasties sections, 12 items, dollars, no cents.", fresh);
t("A5 detected", l.size === "a5" && l.mode === "menu", JSON.stringify(l));
t("extendBrief keeps the original", extendBrief("Brief A $10", "make it bigger") === "Brief A $10\n\nREVISION: make it bigger" && extendBrief("x", "  ") === "x");
const menu = { mode: "poster", restaurantName: "Yiros House", tagline: "", eyebrow: "", sections: [{ title: "Deals", note: null, items: [{ name: "A", description: "", price: "$1", badge: "", photoPrompt: "" }, { name: "B", description: "", price: "$2", badge: "", photoPrompt: "" }] }], footerLeft: "", footerRight: "", followed: ["Used exact prices"], features: [], heroPhoto: { label: "", prompt: "" }, dishPhotos: [],
  engine: { strategy: "single", candidates: [{ provider: "lovable", model: "m", status: "ok", score: 100, issues: [], ms: 1 }], cuisine: [], memory: { exemplars: 0, totalRecorded: 0, judgeRanking: [] }, finalScore: 100, finalIssues: [], totalMs: 1 } };
const text = describeResult(menu, { kind: "new", size: "poster" });
t("result text names venue, count, score", text.includes("Yiros House") && text.includes("2 offers") && text.includes("100/100") && text.includes("Halftone") && text.includes("Followed: Used exact prices"), text);
t("revision wording", describeResult(menu, { kind: "revise", size: "wide" }).startsWith("Updated the poster"));
t("title from venue", conversationTitle("Create a poster for Yiros House with deals") === "Yiros House" && conversationTitle("hello there").length > 0);
console.log(f ? `FAILURES: ${f}` : "ALL PASS"); process.exit(f ? 1 : 0);
