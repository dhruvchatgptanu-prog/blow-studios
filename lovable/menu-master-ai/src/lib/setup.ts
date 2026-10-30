import type { DisplaySpec } from "@/lib/ai/display";
import type { StoreProfile } from "@/lib/ai/store";
import { STORE_TYPE_GUIDE } from "@/lib/ai/store";
import type { Template } from "@/lib/ai/templates";

/**
 * Hand-off between the store wizard (/setup) and the studio (/). Kept in
 * sessionStorage so a refresh keeps the setup and a new tab starts clean.
 */

export type StudioSetup = {
  savedAt: string;
  storeId?: string | undefined;
  displayId?: string | undefined;
  venueName: string;
  profile: StoreProfile;
  template: Pick<Template, "id" | "name" | "style" | "mode" | "theme" | "tagline">;
  display?: DisplaySpec | undefined;
  menuText?: string | undefined;
};

const KEY = "halftone.setup.v1";

export function saveSetup(setup: StudioSetup) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(setup));
  } catch {
    /* storage may be unavailable */
  }
}

export function loadSetup(): StudioSetup | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StudioSetup;
    return parsed && parsed.template && parsed.profile ? parsed : null;
  } catch {
    return null;
  }
}

export function clearSetup() {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/** Condensed profile sent to the engines with every generation. */
export function storeContext(profile: StoreProfile) {
  return {
    storeType: profile.storeType,
    priceTier: profile.priceTier,
    cuisine: profile.cuisine,
    venueName: profile.venueName,
    audience: profile.audience,
    voice: STORE_TYPE_GUIDE[profile.storeType].voice,
    signatureItems: profile.signatureItems.slice(0, 12),
  };
}

/** Builds the studio brief from the wizard inputs so the owner can still edit it. */
export function briefFromSetup(setup: StudioSetup): string {
  const venue = setup.venueName || setup.profile.venueName || "the venue";
  const lines: string[] = [];
  if (setup.template.mode === "poster") lines.push(`Create a ${setup.template.name.toLowerCase()} style deal board for ${venue}.`);
  else lines.push(`Create a ${setup.template.name.toLowerCase()} style menu for ${venue}.`);
  if (setup.menuText?.trim()) lines.push(`Use this current menu exactly (names and prices are final):\n${setup.menuText.trim()}`);
  else lines.push("Use the exact names and prices from the attached menu image.");
  if (setup.display) lines.push(`It will be displayed on a ${setup.display.displayType.replace("-", " ")} of ${setup.display.widthCm} × ${setup.display.heightCm} cm read from about ${setup.display.viewingDistanceM} m, so keep only what fits legibly.`);
  if (setup.profile.signatureItems.length) lines.push(`Signature items to feature: ${setup.profile.signatureItems.slice(0, 4).join(", ")}.`);
  return lines.join(" ");
}

/** Maps a display spec onto the renderer's size keys (custom shapes pick the closest layout family). */
export function rendererSizeFor(display: DisplaySpec | undefined, mode: "menu" | "poster"): "letter" | "a5" | "poster" | "wide" | "fascia-447" | "fascia-180" | "square" {
  if (!display) return mode === "poster" ? "poster" : "letter";
  if (display.format !== "custom") return display.format;
  switch (display.orientation) {
    case "ultra-wide": return display.aspect >= 4.5 ? "fascia-447" : "fascia-180";
    case "landscape": return mode === "poster" ? "wide" : "letter";
    case "square": return "square";
    default: return mode === "poster" ? "poster" : "letter";
  }
}
