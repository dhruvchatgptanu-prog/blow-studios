import { verifyMenu, extractBriefPrices, extractBriefNames, extractDietaryMarkers, dietaryClaims } from "./out/src/lib/ai/verify.js";
import { enrichPhotoPrompt } from "./out/src/lib/ai/cuisine.js";
import { SYSTEM_PROMPT, JUDGE_SYSTEM_PROMPT } from "./out/src/lib/ai/knowledge.js";
console.log("system prompt chars", SYSTEM_PROMPT.length, "judge chars", JUDGE_SYSTEM_PROMPT.length);
const brief = "Create a bold family deals poster for Yiros House. Include: Yiros Meal Deal — 3 lamb yiros and large chips $49.90; Seafood Family Pack — 3 fish, calamari, prawns and chips $59.90; Big Family Yiros — 5 chicken yiros and family chips $69.90. Use the exact names and prices.";
console.log("prices", extractBriefPrices(brief));
console.log("names", extractBriefNames(brief));
const good = { mode:"poster", restaurantName:"Yiros House", tagline:"Family Feast Deals", eyebrow:"Sharing deals", sections:[{title:"SHARING DEALS", note:null, items:[
 {name:"Yiros Meal Deal", description:"3 lamb yiros loaded with salad and tzatziki, plus a large serve of chips.", price:"$49.90", badge:"FEEDS 3", photoPrompt:"Three complete lamb yiros wraps cut diagonally showing grilled lamb, tomato, onion and tzatziki, beside one large wire basket of golden chips on a dark round timber board, softly blurred neutral background, 45-degree angle 50 mm look, large softbox key light from upper left with soft fill, true-to-life colour, square composition with clean edges."},
 {name:"Seafood Family Pack", description:"3 battered fish, calamari, prawns, chips, lemon and tartare.", price:"$59.90", badge:"", photoPrompt:"Complete seafood family platter: three golden battered fish fillets, calamari rings, grilled prawns with tails, chips, lemon halves and tartare in a ramekin on a white oval platter over kraft paper, overhead angle, soft studio light, natural shadows, visible crisp batter texture, square composition, whole platter in frame."},
 {name:"Big Family Yiros", description:"5 chicken yiros with salad and garlic sauce, plus a family serve of chips.", price:"$69.90", badge:"BIG VALUE", photoPrompt:"Five complete chicken yiros wraps fanned on a dark round board with a family basket of chips, visible char on the chicken, lettuce, tomato and garlic sauce, neutral blurred background, 45-degree angle, softbox key light with gentle fill, realistic colour and texture, square composition with clean edges."}]}],
 footerLeft:"ORDER ONLINE", footerRight:"PICKUP · DELIVERY", followed:["Used exact names","Used exact prices"], features:[], heroPhoto:{label:"Family feast", prompt:"A generous family table filled with lamb and chicken yiros wraps, baskets of chips and a seafood platter on dark timber, overhead angle, soft studio light, natural shadows, realistic texture, wide banner crop."}, dishPhotos:[] };
const req = { brief, mode:"poster", style:"bold-deals", pages:"1", size:"poster" };
console.log("good", JSON.stringify(verifyMenu(good, req)));
const bad = JSON.parse(JSON.stringify(good)); bad.sections[0].items[1].price = "$59.00"; bad.sections[0].items[2].name = "Big Family Gyros"; bad.sections[0].items[0].photoPrompt = "Gyros with price text"; bad.footerRight = "CALL 0412 345 678"; bad.features = ["Award winning"];
console.log("bad", JSON.stringify(verifyMenu(bad, req)));
const menuBrief = "Create a one-page menu for Yiros House with sections for yiros, grill plates and sides. Keep the writing concise and use dollar prices.";
console.log("menu-brief prices/names", extractBriefPrices(menuBrief), extractBriefNames(menuBrief));
const euBrief = "Pizza sign for Luigi's. Margherita 12\" €9,50; Diavola 12\" €12; 2 pizzas and 1 garlic bread €24.";
console.log("eu", extractBriefPrices(euBrief), extractBriefNames(euBrief));

// ---- dietary markers, allergen footer, CGI buzzwords (daily run 4) ----
let f = 0; const t = (l, c, d = "") => { if (!c) { f++; console.log("FAIL", l, d); } else console.log("PASS", l); };
const goodScore = verifyMenu(good, req).score;
t("faithful poster scores 100", goodScore === 100, goodScore);
const dietBrief = "One-page menu for Green Fork. Mains: Falafel Wrap (V)(VG) $14, Lamb Yiros (GF) $16, Haloumi Salad (V) $15. Allergens: please ask our staff before ordering.";
const markers = extractDietaryMarkers(dietBrief);
t("markers extracted per item", markers.length === 4 && markers.some((m) => m.name === "Falafel Wrap" && m.marker === "(VG)") && markers.some((m) => m.name === "Lamb Yiros" && m.marker === "(GF)"), JSON.stringify(markers));
t("claims detected", dietaryClaims(dietBrief).includes("vegan") && dietaryClaims(dietBrief).includes("gluten-free") && !dietaryClaims(dietBrief).includes("halal"));
const dietItem = (name, price, badge = "") => ({ name, description: "Fresh and simple.", price, badge, photoPrompt: good.sections[0].items[0].photoPrompt });
const dietGood = { ...good, mode: "menu", restaurantName: "Green Fork", sections: [{ title: "Mains", note: null, items: [dietItem("Falafel Wrap (V)(VG)", "$14"), dietItem("Lamb Yiros (GF)", "$16"), dietItem("Haloumi Salad (V)", "$15")] }], footerLeft: "Allergens: please ask our staff before ordering", footerRight: "", features: [] };
const dietReq = { brief: dietBrief, mode: "menu", style: "modern", pages: "1", size: "a5" };
const dietGoodResult = verifyMenu(dietGood, dietReq);
t("markers kept + allergen footer → no dietary issues", !dietGoodResult.issues.some((i) => /Dietary|allergen/i.test(i)), JSON.stringify(dietGoodResult.issues));
const dietBad = JSON.parse(JSON.stringify(dietGood)); dietBad.sections[0].items[1].name = "Lamb Yiros"; dietBad.sections[0].items[2].description = "Halal haloumi, gluten free."; dietBad.footerLeft = "";
const dietBadResult = verifyMenu(dietBad, dietReq);
t("dropped marker flagged", dietBadResult.issues.some((i) => i.includes("Dietary markers from the brief were dropped: Lamb Yiros (GF)")), JSON.stringify(dietBadResult.issues));
t("invented halal flagged, brief's gluten-free not", dietBadResult.issues.some((i) => i.includes("added: halal") && !i.includes("gluten")), JSON.stringify(dietBadResult.issues));
t("missing allergen footer flagged", dietBadResult.issues.some((i) => i.includes("allergen statement")));
t("score drops for dietary faults", dietBadResult.score <= dietGoodResult.score - 8, `${dietGoodResult.score} → ${dietBadResult.score}`);
const buzz = JSON.parse(JSON.stringify(good)); buzz.sections[0].items[0].photoPrompt += " Ultra-realistic 8k, flawless, masterpiece.";
const buzzResult = verifyMenu(buzz, req);
t("CGI buzzwords penalised", buzzResult.score < 100 && buzzResult.issues.some((i) => i.includes("CGI-style quality words")), JSON.stringify(buzzResult));
const cleaned = enrichPhotoPrompt(buzz.sections[0].items[0].photoPrompt, ["greek / yiros / gyros / souvlaki"]);
t("enrichment strips buzzwords", !/8k|ultra-realistic|flawless|masterpiece/i.test(cleaned) && verifyMenu({ ...buzz, sections: [{ ...buzz.sections[0], items: [{ ...buzz.sections[0].items[0], photoPrompt: cleaned }, ...buzz.sections[0].items.slice(1)] }] }, req).score === 100, cleaned.slice(-120));
t("enrichment idempotent after stripping", enrichPhotoPrompt(cleaned, ["greek / yiros / gyros / souvlaki"]) === cleaned);
console.log(f ? `FAILURES: ${f}` : "ALL PASS"); process.exit(f ? 1 : 0);
