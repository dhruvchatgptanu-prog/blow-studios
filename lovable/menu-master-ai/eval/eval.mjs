import { verifyMenu, extractBriefPrices, extractBriefNames } from "./out/src/lib/ai/verify.js";
import { detectCuisine, enrichPhotoPrompt, enrichMenuPhotos } from "./out/src/lib/ai/cuisine.js";
import { SYSTEM_PROMPT, REPAIR_SYSTEM_PROMPT } from "./out/src/lib/ai/knowledge.js";

const briefs = [
  { name: "greek poster", mode: "poster", size: "poster", brief: "Create a bold family deals poster for Yiros House. Include: Yiros Meal Deal — 3 lamb yiros and large chips $49.90; Seafood Family Pack — 3 fish, calamari, prawns and chips $59.90; Big Family Yiros — 5 chicken yiros and family chips $69.90. Use the exact names and prices.", expectCuisine: "greek / yiros / gyros / souvlaki", prices: 3, names: 3 },
  { name: "pizza sign EU", mode: "poster", size: "square", brief: "12 inch pizza sign for Luigi's Pizzeria. Margherita €9,50; Diavola €12; Family Deal — 2 pizzas and 1 garlic bread €24.", expectCuisine: "pizza", prices: 3, names: 2 },
  { name: "cafe A5", mode: "menu", size: "a5", brief: "Create an A5 brunch menu for Nook Café with sections for coffee, breakfast and toasties. Keep it to 12 items. Prices in dollars, no cents.", expectCuisine: "cafe / breakfast / brunch", prices: 0, names: 0 },
  { name: "indian 2-page", mode: "menu", size: "letter", brief: "Two-page menu for Saffron Palace, Indian restaurant. Sections: Starters, Curries, Tandoori, Breads, Rice, Desserts. Butter Chicken $22, Lamb Rogan Josh $24, Garlic Naan $5. No phone number.", expectCuisine: "indian / curry", prices: 3, names: 3 },
  { name: "burger wide board", mode: "poster", size: "wide", brief: "Wide board for Smash Bros Burgers: 3 offers. Double Smash — 2 patties, cheese, pickles £8.50; Chicken Crunch — crispy chicken burger £7.90; Combo — any burger, fries and drink £11.", expectCuisine: "burgers", prices: 3, names: 3 },
  { name: "kebab fascia", mode: "poster", size: "fascia-447", brief: "Fascia for Istanbul Kebabs. Chicken kebab $14, lamb kebab $15, HSP $16, falafel wrap $12. Halal.", expectCuisine: "kebab / middle eastern", prices: 4, names: 0 },
];

let failures = 0;
for (const b of briefs) {
  const cuisine = detectCuisine(b.brief);
  const prices = extractBriefPrices(b.brief);
  const names = extractBriefNames(b.brief);
  const ok = cuisine[0] === b.expectCuisine && prices.length === b.prices && names.length >= b.names;
  if (!ok) failures++;
  console.log(`${ok ? "PASS" : "FAIL"} ${b.name}: cuisine=${JSON.stringify(cuisine)} prices=${JSON.stringify(prices)} names=${JSON.stringify(names)}`);
}

// enrichment: short prompt should gain surface/angle/light/realism; rich prompt should not be duplicated
const short = enrichPhotoPrompt("Three lamb yiros and a basket of chips.", ["greek / yiros / gyros / souvlaki"]);
const rich = enrichPhotoPrompt("Three complete lamb yiros wraps cut diagonally beside one large wire basket of golden chips on a dark round timber board, softly blurred neutral background, 45-degree angle 50 mm look, large softbox key light from upper left with soft fill, sharp focus, slightly uneven arrangement with natural colour variation, no fake steam, no non-food props.", ["greek / yiros / gyros / souvlaki"]);
console.log("short →", short.split(/\s+/).length, "words:", short);
console.log("rich  →", rich.split(/\s+/).length, "words (should add only the composition line):", rich.slice(-90));
for (const [label, p] of [["short", short], ["rich", rich]]) {
  for (const w of ["text", "logo", "price", "illustration"]) {
    const bad = new RegExp(`\\b${w}\\b`, "i").test(p) && !new RegExp(`\\b(no|without)\\b[^.;]{0,60}\\b${w}\\b`, "i").test(p);
    if (bad) { failures++; console.log(`FAIL ${label} contains unnegated ${w}`); }
  }
}
// verifier must not penalise enriched prompts
const menu = { mode: "poster", restaurantName: "Yiros House", tagline: "Family Feast Deals", eyebrow: "Sharing deals", sections: [{ title: "DEALS", note: null, items: [
  { name: "Yiros Meal Deal", description: "3 lamb yiros with salad and tzatziki, plus a large serve of chips.", price: "$49.90", badge: "FEEDS 3", photoPrompt: "Three lamb yiros and a large basket of chips." },
  { name: "Seafood Family Pack", description: "3 fish, calamari, prawns and chips with lemon.", price: "$59.90", badge: "", photoPrompt: "Seafood platter with three fish, calamari, prawns and chips." },
  { name: "Big Family Yiros", description: "5 chicken yiros with garlic sauce, plus family chips.", price: "$69.90", badge: "BIG VALUE", photoPrompt: "Five chicken yiros and family chips." } ] }],
  footerLeft: "ORDER ONLINE", footerRight: "PICKUP", followed: [], features: [], heroPhoto: { label: "Feast", prompt: "A family table of yiros and chips." }, dishPhotos: [] };
const req = { brief: briefs[0].brief, mode: "poster", style: "bold-deals", pages: "1", size: "poster" };
const before = verifyMenu(menu, req).score, after = verifyMenu(enrichMenuPhotos(menu, ["greek / yiros / gyros / souvlaki"]), req).score;
console.log(`verifier before enrichment ${before} → after ${after} (expect after=100)`);
if (after !== 100) failures++;
console.log("system prompt chars", SYSTEM_PROMPT.length, "repair prompt chars", REPAIR_SYSTEM_PROMPT.length);
console.log(failures ? `FAILURES: ${failures}` : "ALL PASS");
process.exit(failures ? 1 : 0);
