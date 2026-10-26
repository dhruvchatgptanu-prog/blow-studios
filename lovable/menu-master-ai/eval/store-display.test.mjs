import { profileFromText, mergeProfiles } from "./out/src/lib/ai/store.js";
import { recommendTemplates, TEMPLATES } from "./out/src/lib/ai/templates.js";
import { aspectFromCorners, resolveDisplay, contentBudget, fitMenuToDisplay, minLetterHeightCm, orientationOf } from "./out/src/lib/ai/display.js";

let f = 0; const t = (l, c, d = "") => { if (!c) { f++; console.log("FAIL", l, d); } else console.log("PASS", l); };

// ---- store profile baseline ----
const greek = profileFromText("Greek takeaway on a busy high street, counter service, families and tradies at lunch, yellow and black branding #f5c400 #111111, a wide board above the counter and a window poster. Known for lamb yiros.", "Yiros House");
t("takeaway detected", greek.storeType === "takeaway", greek.storeType);
t("palette parsed", greek.palette.length === 2 && greek.palette[0] === "#f5c400");
t("surfaces from text", greek.displaySurfaces.includes("wall-board") && greek.displaySurfaces.includes("window"), JSON.stringify(greek.displaySurfaces));
t("counter flow", greek.orderFlow === "counter");
const fine = profileFromText("Fine dining degustation restaurant, 8-course tasting menu, sommelier, dim candle-lit room, table service.", "Aster");
t("fine dining luxury dim", fine.storeType === "fine-dining" && fine.priceTier === "luxury" && fine.lighting === "dim" && fine.orderFlow === "table-service", JSON.stringify([fine.storeType, fine.priceTier, fine.lighting, fine.orderFlow]));
const truck = profileFromText("Taco food truck at festivals, bright outdoor, 3 m fascia", "Taco Loco");
t("food truck outdoor", truck.storeType === "food-truck" && truck.lighting === "outdoor");
const merged = mergeProfiles(greek, { cuisine: "greek", signatureItems: ["Lamb Yiros"], confidence: 0.9, venueName: "", palette: [] });
t("merge keeps baseline for empty fields", merged.venueName === "Yiros House" && merged.palette.length === 2 && merged.cuisine === "greek" && merged.confidence === 0.9);

// ---- templates ----
const greekRec = recommendTemplates(merged);
t("greek takeaway → street deals first", greekRec[0].template.id === "street-deals", greekRec.map((m) => m.template.id).join(","));
t("4 recommendations with reasons", greekRec.length === 4 && greekRec.every((m) => m.reasons.length > 0));
const fineRec = recommendTemplates(mergeProfiles(fine, { cuisine: "modern australian" }));
t("fine dining → noir fine first", fineRec[0].template.id === "noir-fine", fineRec.map((m) => m.template.id).join(","));
const pizzaRec = recommendTemplates(mergeProfiles(profileFromText("Pizza shop, takeaway and delivery, red and green branding"), { cuisine: "pizza" }));
t("pizza → pizzeria classic first", pizzaRec[0].template.id === "pizzeria-classic", pizzaRec.map((m) => m.template.id).join(","));
t("every template has a renderer style", TEMPLATES.every((tpl) => ["editorial", "modern", "rustic", "bold-deals"].includes(tpl.style)));

// ---- display geometry ----
// A 16:9 TV photographed straight on in a 4:3 image, occupying the middle.
const straight = aspectFromCorners([{ x: 0.1, y: 0.3 }, { x: 0.9, y: 0.3 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }], 4 / 3);
t("straight-on aspect ≈ 1.78", Math.abs(straight - 1.78) < 0.1, String(straight));
// Same board photographed from the left: right edge appears shorter (perspective).
const skewed = aspectFromCorners([{ x: 0.1, y: 0.25 }, { x: 0.85, y: 0.33 }, { x: 0.85, y: 0.83 }, { x: 0.1, y: 0.95 }], 4 / 3);
t("skewed aspect still landscape 1.3-2.2", skewed > 1.3 && skewed < 2.2, String(skewed));
t("orientation buckets", orientationOf(6.2) === "ultra-wide" && orientationOf(1.78) === "landscape" && orientationOf(1) === "square" && orientationOf(0.7) === "portrait");
t("letter height rule", minLetterHeightCm(3) === 2.5 && minLetterHeightCm(12) === 10 && minLetterHeightCm(0.5) === 0.4);

// ---- resolve: user size wins ----
const user = resolveDisplay({ displayType: "wall-board", corners: [], estimatedWidthCm: 0, estimatedHeightCm: 0, references: [], viewingDistanceM: 0, mountedHeightCm: 0, lighting: "neutral", isDigital: false, existingContentSummary: "", confidence: 0, notes: "" }, { widthCm: 180, heightCm: 60 });
t("user size → board 180 format", user.format === "fascia-180" && user.sizeSource === "user" && user.sizeConfidence === 1, JSON.stringify([user.format, user.sizeSource]));
// ---- resolve: model estimate, custom size ----
const model = resolveDisplay({ displayType: "digital-screen", corners: [{ x: 0.1, y: 0.3 }, { x: 0.9, y: 0.3 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }], estimatedWidthCm: 122, estimatedHeightCm: 69, references: [{ object: "55 inch tv", assumedCm: 122, pixelsRelativeToDisplay: 1 }], viewingDistanceM: 4, mountedHeightCm: 180, lighting: "backlit", isDigital: true, existingContentSummary: "three columns of items", confidence: 0.8, notes: "" });
t("55-inch screen → wide format, digital", model.format === "wide" && model.isDigital && model.sizeSource === "model", JSON.stringify([model.format, model.isDigital, model.widthCm]));
// ---- resolve: one dimension + photo aspect ----
const oneDim = resolveDisplay({ displayType: "fascia", corners: [{ x: 0.02, y: 0.4 }, { x: 0.98, y: 0.4 }, { x: 0.98, y: 0.55 }, { x: 0.02, y: 0.55 }], estimatedWidthCm: 0, estimatedHeightCm: 0, references: [], viewingDistanceM: 10, mountedHeightCm: 0, lighting: "outdoor", isDigital: false, existingContentSummary: "", confidence: 0.2, notes: "" }, { widthCm: 500 });
t("fascia width + aspect → ultra-wide custom", oneDim.orientation === "ultra-wide" && oneDim.format === "custom" && oneDim.heightCm > 40 && oneDim.heightCm < 80, JSON.stringify([oneDim.orientation, oneDim.format, oneDim.heightCm]));
t("fascia budget tiny descriptions", oneDim.budget.maxDescriptionWords <= 8 && oneDim.budget.minLetterHeightCm === 8.3, JSON.stringify(oneDim.budget));
// ---- resolve: nothing known ----
const nothing = resolveDisplay(undefined, {});
t("no data → 16:9 default with note", nothing.format === "wide" && nothing.notes.length === 1);

// ---- budgets scale sensibly ----
const small = contentBudget(21, 29.7, 0.5, "portrait", false), big = contentBudget(180, 60, 3, "landscape", false), fascia = contentBudget(447, 71.5, 12, "ultra-wide", false);
t("A4 table menu holds many items", small.maxItems >= 20, JSON.stringify(small));
t("board holds fewer than table menu", big.maxItems < small.maxItems && big.maxOffers >= 2, JSON.stringify(big));
t("fascia holds few offers", fascia.maxOffers <= 6 && fascia.maxItems <= 12, JSON.stringify(fascia));

// ---- fit ----
const menu = { mode: "poster", restaurantName: "X", tagline: "T", eyebrow: "E", sections: [{ title: "A", note: null, items: Array.from({ length: 6 }, (_, i) => ({ name: `Item ${i + 1}`, description: "one two three four five six seven eight nine ten eleven twelve", price: "$1", badge: "", photoPrompt: "p" })) }], footerLeft: "", footerRight: "", followed: ["x"], features: [], heroPhoto: { label: "h", prompt: "p" }, dishPhotos: [] };
const fit = fitMenuToDisplay(menu, { ...oneDim, budget: { ...oneDim.budget, maxOffers: 4, maxDescriptionWords: 5 } });
t("fit trims to 4 offers and lists omitted", fit.menu.sections[0].items.length === 4 && fit.omitted.length === 2 && fit.menu.followed.some((l) => l.startsWith("Omitted for legibility")), JSON.stringify(fit.omitted));
t("fit shortens descriptions", fit.menu.sections[0].items[0].description.split(" ").length === 5 && fit.trimmedDescriptions === 4);
const noChange = fitMenuToDisplay(menu, { ...user, budget: { ...user.budget, maxOffers: 10, maxDescriptionWords: 20 } });
t("fit leaves fitting menus untouched", noChange.changed === false && noChange.menu === menu);

console.log(f ? `FAILURES: ${f}` : "ALL PASS"); process.exit(f ? 1 : 0);
