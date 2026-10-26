import { z } from "zod/v4";

import type { GeneratedMenuCore, OutputSize } from "./schema";

/**
 * Display measurement and menu fitting.
 *
 * A photo of the display (wall board, screen, window, A-frame, fascia) is sent
 * to a vision engine that returns the structured measurement below: what the
 * display is, where its corners are in the image, how big it is relative to
 * reference objects in the scene, and how far people read it from. Everything
 * after that is deterministic: aspect ratio from the corners (with a
 * perspective correction), nearest print format or a custom one, letter-height
 * minimums from the viewing distance, and a content budget the menu is fitted
 * to.
 */

export const DISPLAY_TYPES = ["wall-board", "digital-screen", "window", "counter-card", "a-frame", "fascia", "table-menu", "drive-thru-board", "unknown"] as const;
export type DisplayType = (typeof DISPLAY_TYPES)[number];
export type Orientation = "ultra-wide" | "landscape" | "square" | "portrait" | "tall";

/** Real-world sizes of things that commonly appear in venue photos (cm). */
export const REFERENCE_OBJECTS: Record<string, { widthCm?: number; heightCm?: number; note: string }> = {
  "interior door": { widthCm: 82, heightCm: 204, note: "Standard hinged door; height is the most reliable" },
  "shop door": { widthCm: 90, heightCm: 210, note: "Commercial entrance door leaf" },
  "a4 sheet": { widthCm: 21, heightCm: 29.7, note: "Printed notice or poster" },
  "a3 sheet": { widthCm: 29.7, heightCm: 42, note: "" },
  "brick": { widthCm: 23, heightCm: 7.6, note: "Face brick; count courses for height" },
  "ceiling tile": { widthCm: 60, heightCm: 60, note: "Suspended ceiling grid" },
  "light switch": { widthCm: 8.6, heightCm: 8.6, note: "AU/UK plate" },
  "power outlet": { widthCm: 11.5, heightCm: 7, note: "AU double outlet plate" },
  "counter height": { heightCm: 90, note: "Service counter top from floor" },
  "bar height": { heightCm: 105, note: "Bar top from floor" },
  "chair seat": { heightCm: 45, note: "Dining chair seat from floor" },
  "table top": { heightCm: 75, note: "Dining table from floor" },
  "43 inch tv": { widthCm: 96, heightCm: 54, note: "16:9 panel, bezel excluded" },
  "50 inch tv": { widthCm: 111, heightCm: 62, note: "" },
  "55 inch tv": { widthCm: 122, heightCm: 69, note: "" },
  "65 inch tv": { widthCm: 144, heightCm: 81, note: "" },
  "person": { heightCm: 170, note: "Average adult standing" },
  "soft drink can": { widthCm: 6.6, heightCm: 12.3, note: "375 ml" },
  "2 litre bottle": { heightCm: 32, note: "" },
  "takeaway coffee cup": { heightCm: 12, note: "12 oz" },
  "floor tile": { widthCm: 60, heightCm: 60, note: "Common commercial tile; 30 cm also common" },
};

export const displayMeasurementSchema = z.object({
  displayType: z.enum(DISPLAY_TYPES),
  /** Four corners of the display in the image, normalised 0-1, order: top-left, top-right, bottom-right, bottom-left. */
  corners: z.array(z.object({ x: z.number(), y: z.number() })),
  /** Physical size estimate in cm; 0 when unknown. */
  estimatedWidthCm: z.number(),
  estimatedHeightCm: z.number(),
  /** Reference objects used and their assumed sizes. */
  references: z.array(z.object({ object: z.string(), assumedCm: z.number(), pixelsRelativeToDisplay: z.number() })),
  /** Typical reading distance in metres (counter ≈ 2, across a room ≈ 5, street ≈ 12). */
  viewingDistanceM: z.number(),
  mountedHeightCm: z.number(),
  lighting: z.enum(["bright", "neutral", "dim", "outdoor", "backlit"]),
  isDigital: z.boolean(),
  existingContentSummary: z.string(),
  confidence: z.number(),
  notes: z.string(),
});
export type DisplayMeasurement = z.infer<typeof displayMeasurementSchema>;

export const DISPLAY_MEASUREMENT_JSON_SCHEMA = {
  type: "object",
  properties: {
    displayType: { type: "string", enum: [...DISPLAY_TYPES] },
    corners: { type: "array", items: { type: "object", properties: { x: { type: "number" }, y: { type: "number" } }, required: ["x", "y"], additionalProperties: false } },
    estimatedWidthCm: { type: "number" },
    estimatedHeightCm: { type: "number" },
    references: { type: "array", items: { type: "object", properties: { object: { type: "string" }, assumedCm: { type: "number" }, pixelsRelativeToDisplay: { type: "number" } }, required: ["object", "assumedCm", "pixelsRelativeToDisplay"], additionalProperties: false } },
    viewingDistanceM: { type: "number" },
    mountedHeightCm: { type: "number" },
    lighting: { type: "string", enum: ["bright", "neutral", "dim", "outdoor", "backlit"] },
    isDigital: { type: "boolean" },
    existingContentSummary: { type: "string" },
    confidence: { type: "number" },
    notes: { type: "string" },
  },
  required: ["displayType", "corners", "estimatedWidthCm", "estimatedHeightCm", "references", "viewingDistanceM", "mountedHeightCm", "lighting", "isDigital", "existingContentSummary", "confidence", "notes"],
  additionalProperties: false,
} as const;

export const DISPLAY_MEASUREMENT_PROMPT = [
  "You are a signage surveyor. The attached photo shows a menu display (board, screen, window, A-frame, fascia, counter card). Measure it.",
  "corners: the four corners of the display surface itself (not its frame) as normalised image coordinates 0-1, in the order top-left, top-right, bottom-right, bottom-left. If a corner is cut off by the photo edge, extrapolate and say so in notes.",
  `estimatedWidthCm / estimatedHeightCm: physical size of the display. Use reference objects at a similar depth in the scene and list each one you used with the size you assumed. Known sizes: ${Object.entries(REFERENCE_OBJECTS).map(([name, size]) => `${name} ${size.widthCm ? `${size.widthCm}w` : ""}${size.widthCm && size.heightCm ? "×" : ""}${size.heightCm ? `${size.heightCm}h` : ""} cm`).join("; ")}. For a TV, read the bezel and panel proportions and pick the closest standard diagonal. Return 0 for a dimension you cannot estimate.`,
  "viewingDistanceM: how far a customer typically stands when reading it (behind a counter ≈ 2 m, across a room ≈ 4-6 m, from the street ≈ 8-15 m). mountedHeightCm: bottom edge above floor, 0 if unknown.",
  "existingContentSummary: in one or two sentences, what the display currently shows (sections, rough item count, photo use), so the new design can respect what works.",
  "confidence 0-1 reflects how reliable the size estimate is. Be honest: a photo with no reference objects deserves confidence ≤ 0.3.",
  "Return JSON only.",
].join("\n");

// ---------- geometry ----------

type Point = { x: number; y: number };

function distance(a: Point, b: Point) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * Aspect ratio (width / height) of a quadrilateral seen in perspective.
 * Uses the mean of opposite edges, then corrects for foreshortening with a
 * simple vanishing-angle heuristic: the more the top and bottom edges differ
 * in length, the more the camera was tilted vertically (and vice versa).
 */
export function aspectFromCorners(corners: Point[], imageAspect = 4 / 3): number | null {
  if (corners.length !== 4) return null;
  const [tl, tr, br, bl] = corners as [Point, Point, Point, Point];
  // Work in image pixel space so x and y are comparable.
  const scale = (p: Point) => ({ x: p.x * imageAspect, y: p.y });
  const a = scale(tl), b = scale(tr), c = scale(br), d = scale(bl);
  const top = distance(a, b), bottom = distance(d, c), left = distance(a, d), right = distance(b, c);
  if (!top || !bottom || !left || !right) return null;
  // Foreshortening: if one pair of opposite sides differs a lot, the true length is closer to the longer one.
  const width = Math.max(top, bottom) * 0.6 + Math.min(top, bottom) * 0.4;
  const height = Math.max(left, right) * 0.6 + Math.min(left, right) * 0.4;
  const ratio = width / height;
  return Number.isFinite(ratio) && ratio > 0.05 && ratio < 20 ? ratio : null;
}

export function orientationOf(aspect: number): Orientation {
  if (aspect >= 2.6) return "ultra-wide";
  if (aspect >= 1.2) return "landscape";
  if (aspect > 0.85) return "square";
  if (aspect > 0.45) return "portrait";
  return "tall";
}

export const KNOWN_FORMATS: { key: OutputSize; widthCm: number; heightCm: number; label: string }[] = [
  { key: "fascia-447", widthCm: 447, heightCm: 71.5, label: "Fascia 447 × 71.5 cm" },
  { key: "fascia-180", widthCm: 180, heightCm: 60.4, label: "Board 180 × 60.4 cm" },
  { key: "wide", widthCm: 160, heightCm: 90, label: "Wide board 16:9" },
  { key: "poster", widthCm: 59.4, heightCm: 84.1, label: "Portrait poster A1" },
  { key: "square", widthCm: 30.5, heightCm: 30.5, label: "12-inch square sign" },
  { key: "letter", widthCm: 21.6, heightCm: 27.9, label: "US Letter" },
  { key: "a5", widthCm: 14.8, heightCm: 21, label: "A5" },
];

export type ContentBudget = {
  /** Smallest readable cap height for item names at the viewing distance, cm. */
  minLetterHeightCm: number;
  maxOffers: number;
  maxItems: number;
  maxSections: number;
  maxNameWords: number;
  maxDescriptionWords: number;
  photosRecommended: number;
};

export type DisplaySpec = {
  displayType: DisplayType;
  widthCm: number;
  heightCm: number;
  aspect: number;
  orientation: Orientation;
  /** Existing renderer format when one is close enough, otherwise "custom". */
  format: OutputSize | "custom";
  formatLabel: string;
  viewingDistanceM: number;
  lighting: DisplayMeasurement["lighting"];
  isDigital: boolean;
  sizeConfidence: number;
  budget: ContentBudget;
  sizeSource: "user" | "model" | "aspect-only";
  notes: string[];
};

export type DisplayOverrides = { widthCm?: number | undefined; heightCm?: number | undefined; viewingDistanceM?: number | undefined; imageAspect?: number | undefined };

/** Letter height rule of thumb: ~2.5 cm of cap height per 3 m of viewing distance; floor 0.3 cm (≈12 pt) for hand-held menus. */
export function minLetterHeightCm(viewingDistanceM: number) {
  return Math.max(0.3, Math.round((viewingDistanceM / 3) * 2.5 * 10) / 10);
}

export function contentBudget(widthCm: number, heightCm: number, viewingDistanceM: number, orientation: Orientation, digital: boolean): ContentBudget {
  const letter = minLetterHeightCm(viewingDistanceM);
  // A text line needs roughly 2.2× the cap height (ascenders, descenders, leading).
  const lineCm = letter * 2.2;
  const usableHeight = heightCm * 0.72; // minus brand zone, margins, footer
  const rows = Math.max(1, Math.floor(usableHeight / lineCm));
  const charsPerLine = Math.max(10, Math.floor(widthCm / (letter * 0.6)));
  // How many text columns the width supports at this letter size.
  const textColumns = orientation === "ultra-wide"
    ? Math.min(6, Math.max(2, Math.floor(widthCm / (letter * 12))))
    : orientation === "landscape"
      ? Math.min(3, Math.max(1, Math.floor(widthCm / (letter * 26))))
      : charsPerLine >= 70 ? 2 : 1;
  // Lines per item: name plus a short description, less on ultra-wide where descriptions are rare.
  const linesPerItem = orientation === "ultra-wide" ? 1.2 : 1.6;
  const linesPerOffer = orientation === "ultra-wide" ? 2.5 : 5; // photo-led offer row
  const maxItems = Math.max(2, Math.min(40, Math.floor(rows / linesPerItem) * textColumns));
  const offerColumns = orientation === "ultra-wide" ? Math.min(5, Math.max(2, Math.floor(widthCm / (letter * 14)))) : orientation === "landscape" ? Math.min(3, Math.max(1, Math.floor(widthCm / (letter * 30)))) : 1;
  const maxOffers = Math.max(1, Math.min(6, Math.max(1, Math.floor(rows / linesPerOffer)) * offerColumns));
  const maxNameWords = Math.max(2, Math.min(6, Math.floor(charsPerLine / (textColumns * 9))));
  const maxDescriptionWords = orientation === "ultra-wide" ? Math.min(8, Math.max(0, Math.floor(charsPerLine / (textColumns * 7)))) : Math.min(14, Math.max(4, Math.floor(charsPerLine / (textColumns * 6))));
  const maxSections = Math.max(1, Math.min(8, Math.floor(maxItems / 4)));
  const photosRecommended = digital || orientation === "ultra-wide" ? Math.min(maxOffers, 4) : Math.min(maxOffers, 3);
  return { minLetterHeightCm: letter, maxOffers, maxItems, maxSections, maxNameWords, maxDescriptionWords, photosRecommended };
}

/** Turns a raw measurement (plus optional user-entered sizes) into the spec the studio and the engines use. */
export function resolveDisplay(measurement: Partial<DisplayMeasurement> | undefined, overrides: DisplayOverrides = {}): DisplaySpec {
  const notes: string[] = [];
  const cornersAspect = measurement?.corners ? aspectFromCorners(measurement.corners, overrides.imageAspect ?? 4 / 3) : null;
  let widthCm = overrides.widthCm ?? 0;
  let heightCm = overrides.heightCm ?? 0;
  let sizeSource: DisplaySpec["sizeSource"] = "aspect-only";
  if (widthCm > 0 && heightCm > 0) sizeSource = "user";
  else if (widthCm > 0 || heightCm > 0) {
    // One user dimension plus an aspect from the photo or the model.
    const aspect = cornersAspect ?? (measurement?.estimatedWidthCm && measurement.estimatedHeightCm ? measurement.estimatedWidthCm / measurement.estimatedHeightCm : 16 / 9);
    if (widthCm > 0) heightCm = widthCm / aspect; else widthCm = heightCm * aspect;
    sizeSource = "user";
    notes.push("One dimension was supplied; the other was derived from the photo's aspect ratio.");
  } else if (measurement?.estimatedWidthCm && measurement.estimatedHeightCm) {
    widthCm = measurement.estimatedWidthCm;
    heightCm = measurement.estimatedHeightCm;
    sizeSource = "model";
    if (cornersAspect && Math.abs(cornersAspect - widthCm / heightCm) / cornersAspect > 0.25) {
      notes.push(`The corner geometry suggests an aspect of ${cornersAspect.toFixed(2)} while the size estimate implies ${(widthCm / heightCm).toFixed(2)}; the geometry was trusted for the shape, the estimate for the scale.`);
      const area = widthCm * heightCm;
      widthCm = Math.sqrt(area * cornersAspect);
      heightCm = area / widthCm;
    }
  } else if (cornersAspect) {
    // Shape only: assume a plausible size for the display type so budgets are sensible.
    const assumed: Record<DisplayType, number> = { "wall-board": 120, "digital-screen": 122, window: 100, "counter-card": 21, "a-frame": 60, fascia: 400, "table-menu": 21, "drive-thru-board": 150, unknown: 100 };
    widthCm = assumed[measurement?.displayType ?? "unknown"];
    heightCm = widthCm / cornersAspect;
    notes.push("No size could be estimated; a typical size for this display type was assumed. Enter the real width or height for exact fitting.");
  } else {
    widthCm = 160; heightCm = 90;
    notes.push("No measurement available; defaulting to a 16:9 wide board.");
  }
  widthCm = Math.round(widthCm * 10) / 10;
  heightCm = Math.round(heightCm * 10) / 10;
  const aspect = widthCm / heightCm;
  const orientation = orientationOf(aspect);
  const viewingDistanceM = overrides.viewingDistanceM ?? (measurement?.viewingDistanceM && measurement.viewingDistanceM > 0 ? measurement.viewingDistanceM : defaultViewingDistance(measurement?.displayType ?? "unknown", orientation));
  const digital = Boolean(measurement?.isDigital) || measurement?.displayType === "digital-screen";

  // Nearest known format: same orientation and within 12% on aspect and 25% on width.
  let format: DisplaySpec["format"] = "custom";
  let formatLabel = `Custom ${widthCm} × ${heightCm} cm`;
  for (const known of KNOWN_FORMATS) {
    const knownAspect = known.widthCm / known.heightCm;
    if (Math.abs(knownAspect - aspect) / knownAspect <= 0.12 && (sizeSource === "aspect-only" || Math.abs(known.widthCm - widthCm) / known.widthCm <= 0.25)) {
      format = known.key;
      formatLabel = known.label;
      break;
    }
  }
  const budget = contentBudget(widthCm, heightCm, viewingDistanceM, orientation, digital);
  if (measurement?.notes) notes.push(measurement.notes);
  return {
    displayType: measurement?.displayType ?? "unknown",
    widthCm,
    heightCm,
    aspect: Math.round(aspect * 1000) / 1000,
    orientation,
    format,
    formatLabel,
    viewingDistanceM,
    lighting: measurement?.lighting ?? "neutral",
    isDigital: digital,
    sizeConfidence: sizeSource === "user" ? 1 : sizeSource === "model" ? Math.min(1, Math.max(0, measurement?.confidence ?? 0.3)) : 0.2,
    budget,
    sizeSource,
    notes,
  };
}

function defaultViewingDistance(type: DisplayType, orientation: Orientation) {
  switch (type) {
    case "fascia": return 10;
    case "drive-thru-board": return 3;
    case "digital-screen": return 4;
    case "wall-board": return orientation === "ultra-wide" ? 5 : 3.5;
    case "window": return 2.5;
    case "a-frame": return 2;
    case "counter-card": return 0.8;
    case "table-menu": return 0.5;
    default: return orientation === "ultra-wide" ? 6 : 3;
  }
}

/** Lines the engines receive so they design for the real display. */
export function displayBriefLines(spec: DisplaySpec) {
  const b = spec.budget;
  return [
    `DISPLAY: ${spec.displayType.replace("-", " ")}, ${spec.widthCm} × ${spec.heightCm} cm (${spec.orientation}, aspect ${spec.aspect}), ${spec.isDigital ? "digital screen" : "printed"}, read from about ${spec.viewingDistanceM} m in ${spec.lighting} light.`,
    `LEGIBILITY BUDGET: item names need ≥ ${b.minLetterHeightCm} cm cap height, so the design may hold at most ${b.maxItems} items (${b.maxOffers} photo-led offers) across ≤ ${b.maxSections} sections; names ≤ ${b.maxNameWords} words, descriptions ≤ ${b.maxDescriptionWords} words${b.maxDescriptionWords === 0 ? " (no descriptions)" : ""}; ${b.photosRecommended} photos at most.`,
    "If the supplied menu has more items than fit, keep the exact items the brief marks as priorities, then the first items of each section, and list what you left out in 'followed' as 'Omitted for legibility: …'. Never shrink type below the budget to squeeze items in.",
  ];
}

export type FitResult = { menu: GeneratedMenuCore; omitted: string[]; trimmedDescriptions: number; changed: boolean };

/**
 * Deterministic last-mile fit: enforces the budget on a finished menu so the
 * rendered design is legible even if an engine ignored the numbers.
 */
export function fitMenuToDisplay(menu: GeneratedMenuCore, spec: DisplaySpec): FitResult {
  const b = spec.budget;
  const omitted: string[] = [];
  let trimmed = 0;
  let changed = false;
  const limit = menu.mode === "poster" ? b.maxOffers : b.maxItems;
  let remaining = limit;
  const sections = menu.sections.slice(0, Math.max(1, b.maxSections)).map((section) => {
    const keep = section.items.slice(0, Math.max(0, remaining));
    const drop = section.items.slice(keep.length);
    remaining -= keep.length;
    omitted.push(...drop.map((item) => item.name));
    const items = keep.map((item) => {
      const words = item.description.trim().split(/\s+/).filter(Boolean);
      if (b.maxDescriptionWords === 0 && item.description) { trimmed++; return { ...item, description: "" }; }
      if (words.length > b.maxDescriptionWords) { trimmed++; return { ...item, description: `${words.slice(0, b.maxDescriptionWords).join(" ").replace(/[,;:]$/, "")}` }; }
      return item;
    });
    return { ...section, items };
  });
  for (const section of menu.sections.slice(sections.length)) omitted.push(...section.items.map((item) => item.name));
  const kept = sections.filter((section) => section.items.length > 0);
  changed = omitted.length > 0 || trimmed > 0 || kept.length !== menu.sections.length;
  const followed = omitted.length ? [...menu.followed.filter((line) => !line.startsWith("Omitted for legibility")), `Omitted for legibility: ${omitted.join(", ")}`] : menu.followed;
  return { menu: changed ? { ...menu, sections: kept.length ? kept : menu.sections, followed } : menu, omitted, trimmedDescriptions: trimmed, changed };
}
