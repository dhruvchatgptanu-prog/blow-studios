Rounds 3-5 upgrade, MESSAGE 2 of 2 (server functions, UI, routes). After writing these files run the full type check; fix only genuine compile errors with type-only changes. The TanStack Router plugin regenerates src/routeTree.gen.ts; the copy below just saves a restart.

Apply EXACTLY as given; overwrite/create each file verbatim; do not refactor, rename or "improve"; keep your explanation to a few lines. Do NOT touch src/lib/image-gateway.server.ts, src/routes/api/generate-image.ts, src/routes/api/edit-image.ts or src/lib/streamImage.ts. The Supabase tables public.halftone_stores, public.halftone_displays and the new columns on public.halftone_generations ALREADY EXIST (RLS enabled) — do not create, alter or migrate them.



===== FILE: src/lib/menu.functions.ts =====
```tsx
import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { composeMenu } from "@/lib/ai/ensemble.server";
import type { GeneratedMenu, MenuRequest } from "@/lib/ai/schema";

export type { EngineCandidate, EngineReport, GeneratedMenu, MenuItem, MenuSection, PhotoBrief } from "@/lib/ai/schema";
export { stripEngine } from "@/lib/ai/schema";

const referenceImageSchema = z.string().max(12_000_000).refine((value) => {
  const match = /^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$/.exec(value);
  if (!match) return false;
  const mime = match[1];
  const head = match[2]?.slice(0, 16) ?? "";
  if (mime === "image/png") return head.startsWith("iVBORw0KGgo");
  if (mime === "image/jpeg") return head.startsWith("/9j/");
  return mime === "image/webp" && head.startsWith("UklGR");
}, "Upload a valid JPG, PNG or WebP menu image.");

const inputSchema = z.object({
  brief: z.string().min(3).max(4000),
  style: z.enum(["editorial", "modern", "rustic", "bold-deals"]),
  mode: z.enum(["menu", "poster"]),
  pages: z.enum(["1", "2"]),
  size: z.enum(["letter", "a5", "poster", "wide", "fascia-447", "fascia-180", "square"]),
  previous: z.string().max(60_000).optional(),
  referenceImage: referenceImageSchema.optional(),
  template: z.string().max(60).optional(),
  display: z
    .object({
      displayType: z.string(),
      widthCm: z.number().positive(),
      heightCm: z.number().positive(),
      aspect: z.number().positive(),
      orientation: z.enum(["ultra-wide", "landscape", "square", "portrait", "tall"]),
      format: z.string(),
      formatLabel: z.string(),
      viewingDistanceM: z.number().positive(),
      lighting: z.enum(["bright", "neutral", "dim", "outdoor", "backlit"]),
      isDigital: z.boolean(),
      sizeConfidence: z.number(),
      budget: z.object({ minLetterHeightCm: z.number(), maxOffers: z.number(), maxItems: z.number(), maxSections: z.number(), maxNameWords: z.number(), maxDescriptionWords: z.number(), photosRecommended: z.number() }),
      sizeSource: z.enum(["user", "model", "aspect-only"]),
      notes: z.array(z.string()),
    })
    .optional(),
  store: z.object({ storeType: z.string(), priceTier: z.string(), cuisine: z.string(), venueName: z.string(), audience: z.string(), voice: z.string() }).optional(),
  storeId: z.string().uuid().optional(),
  displayId: z.string().uuid().optional(),
});

/**
 * Generate a menu with every configured engine at once (Halftone built-in via
 * the Lovable gateway, ChatGPT via OpenAI, Claude via Anthropic), verify each
 * candidate deterministically, then let the judge merge the strongest parts.
 * See `src/lib/ai/ensemble.server.ts`.
 */
export const generateMenu = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => inputSchema.parse(data))
  .handler(async ({ data }): Promise<GeneratedMenu> => composeMenu(data as MenuRequest));

```

===== FILE: src/lib/store.functions.ts =====
```tsx
import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { analyzeStoreOnServer, measureDisplayOnServer } from "@/lib/ai/store.server";
import type { DisplaySpec } from "@/lib/ai/display";
import type { TemplateMatch } from "@/lib/ai/templates";
import type { StoreProfile } from "@/lib/ai/store";

export type { DisplaySpec } from "@/lib/ai/display";
export type { StoreProfile } from "@/lib/ai/store";
export type { Template, TemplateMatch } from "@/lib/ai/templates";

const imageSchema = z.string().max(12_000_000).regex(/^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/, "Upload a JPG, PNG or WebP image.");

const analyzeInput = z.object({
  description: z.string().min(10).max(3000),
  venueName: z.string().max(120).optional(),
  images: z.array(imageSchema).max(4).optional(),
});

const measureInput = z.object({
  image: imageSchema,
  imageAspect: z.number().min(0.2).max(5).optional(),
  widthCm: z.number().min(5).max(3000).optional(),
  heightCm: z.number().min(5).max(3000).optional(),
  viewingDistanceM: z.number().min(0.3).max(50).optional(),
  storeId: z.string().uuid().optional(),
  hint: z.string().max(500).optional(),
});

export type AnalyzeStoreResult = { storeId?: string | undefined; profile: StoreProfile; templates: TemplateMatch[]; engine: { provider: string; model: string } | null; warnings: string[] };
export type MeasureDisplayResult = { displayId?: string | undefined; spec: DisplaySpec; engine: { provider: string; model: string } | null; warnings: string[] };

/** Step 1 of the wizard: description + photos → store profile + recommended templates. */
export const analyzeStore = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => analyzeInput.parse(data))
  .handler(async ({ data }): Promise<AnalyzeStoreResult> => analyzeStoreOnServer(data));

/** Step 2 of the wizard: display photo (+ optional known size) → display spec with a legibility budget. */
export const measureDisplay = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => measureInput.parse(data))
  .handler(async ({ data }): Promise<MeasureDisplayResult> => measureDisplayOnServer(data));

```

===== FILE: src/lib/setup.ts =====
```tsx
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

```

===== FILE: src/components/BriefCoach.tsx =====
```tsx
import { Lightbulb, TriangleAlert } from "lucide-react";
import { useMemo } from "react";

import { coachBrief } from "@/lib/ai/coach";

/** Instant, rule-based hints under the brief. Runs in the browser; no AI, no credits. */
export function BriefCoach({ brief, mode }: { brief: string; mode: "menu" | "poster" }) {
  const report = useMemo(() => coachBrief(brief, mode), [brief, mode]);
  if (report.hints.length === 0) return null;
  const fixes = report.hints.filter((hint) => hint.level === "fix");
  return (
    <div className="mt-2 border-l-2 border-line pl-3">
      <p className="font-mono text-[9px] font-bold uppercase text-muted">Brief check · {report.score >= 85 ? "ready" : fixes.length ? "needs detail" : "could be sharper"}</p>
      <ul className="mt-1 space-y-1">
        {report.hints.map((hint) => (
          <li key={hint.text} className="flex items-start gap-1.5 text-[11px] leading-snug text-muted">
            {hint.level === "fix" ? <TriangleAlert className="mt-0.5 size-3 shrink-0 text-press" /> : <Lightbulb className="mt-0.5 size-3 shrink-0 text-herb" />}
            <span>{hint.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

```

===== FILE: src/components/MenuSheet.tsx =====
```tsx
import { Check, Flame, Sparkles } from "lucide-react";
import type { CSSProperties } from "react";

import type { DisplaySpec } from "@/lib/ai/display";
import type { GeneratedMenu } from "@/lib/menu.functions";

export type StyleKey = "editorial" | "modern" | "rustic" | "bold-deals";
export type OutputSize = "letter" | "a5" | "poster" | "wide" | "fascia-447" | "fascia-180" | "square";
export type Shot = { label: string; src: string; final: boolean };
/** CSS variable overrides from a template (see src/lib/ai/templates.ts). */
export type SheetTheme = Record<string, string>;

const styleClasses: Record<Exclude<StyleKey, "bold-deals">, { name: string; item: string; section: string; title: string }> = {
  editorial: { name: "font-display font-black", item: "font-display font-semibold", section: "font-mono font-bold", title: "text-5xl" },
  modern: { name: "font-body font-bold uppercase", item: "font-body font-semibold", section: "font-body font-bold uppercase", title: "text-4xl" },
  rustic: { name: "font-mono font-bold uppercase", item: "font-display font-semibold italic", section: "font-mono font-bold uppercase", title: "text-4xl" },
};

function SignPhoto({ shot, label }: { shot: Shot | undefined; label: string }) {
  return shot ? <img src={shot.src} alt={label} className={`h-full w-full object-cover ${shot.final ? "" : "blur-lg"}`} /> : <Flame aria-hidden="true" className="h-1/3 w-1/3 text-poster/40" />;
}

function FasciaSheet({ menu, size, dishes, aspect }: { menu: GeneratedMenu; size: "fascia-447" | "fascia-180"; dishes: Shot[]; aspect?: number | undefined }) {
  const offers = menu.sections.flatMap((section) => section.items);
  return <div className="sign-scroll mx-auto max-w-[1100px] overflow-x-auto no-print-scroll">
    <div className={`print-sheet sign-sheet ${aspect ? "sign-custom" : ""} sign-${size} relative mx-auto overflow-hidden bg-sun text-ink shadow-2xl`} style={aspect ? { aspectRatio: String(aspect) } : undefined}>
      <div className="sign-brand flex min-w-0 flex-col justify-center bg-ink text-poster-foreground">
        <p className="sign-eyebrow font-mono font-bold uppercase text-sun">{menu.eyebrow}</p>
        <h2 className="sign-brand-name break-words font-impact uppercase leading-none">{menu.restaurantName}</h2>
        {menu.tagline ? <p className="sign-tagline font-impact uppercase text-sun">{menu.tagline}</p> : null}
      </div>
      <div className="sign-main flex min-w-0 flex-col justify-between">
        <p className="sign-categories border-b border-ink/30 font-impact uppercase">{menu.sections.map((section) => section.title).join("  •  ")}</p>
        <div className="sign-offers flex min-h-0 min-w-0 flex-1 items-center justify-around">
          {offers.map((offer, index) => <div key={`${offer.name}-${index}`} className="sign-offer flex min-w-0 flex-1 items-center">
            <div className="sign-photo grid shrink-0 place-items-center overflow-hidden rounded-full border-2 border-ink/20 bg-card"><SignPhoto shot={dishes[index]} label={offer.name} /></div>
            <div className="sign-offer-copy min-w-0"><p className="sign-offer-name break-words font-impact uppercase leading-none">{offer.name}</p>{offer.description ? <p className="sign-description line-clamp-2 font-body leading-tight">{offer.description}</p> : null}<p className="sign-price font-impact leading-none text-poster">{offer.price}</p></div>
          </div>)}
        </div>
        <p className="sign-footer truncate border-t border-ink/30 font-impact uppercase">{[menu.footerLeft, menu.footerRight].filter(Boolean).join("  •  ")}</p>
      </div>
    </div>
  </div>;
}

function SquareSign({ menu, dishes }: { menu: GeneratedMenu; dishes: Shot[] }) {
  const offers = menu.sections.flatMap((section) => section.items);
  const first = offers[0];
  return <div className="print-sheet square-sign poster-texture relative mx-auto flex w-full max-w-[560px] flex-col overflow-hidden bg-sun text-ink shadow-2xl">
    <div className="bg-ink px-5 py-3 text-center font-impact text-sm uppercase text-poster-foreground">{menu.restaurantName}</div>
    <div className="px-5 pt-5 text-center"><p className="font-mono text-xs font-bold uppercase text-poster">{menu.eyebrow}</p><h2 className="mt-1 break-words font-impact text-4xl uppercase leading-none sm:text-5xl">{menu.tagline || first?.name}</h2></div>
    <div className="relative mx-auto mt-3 grid aspect-square w-[56%] place-items-center overflow-hidden rounded-full border-4 border-ink/20 bg-card shadow-xl"><SignPhoto shot={dishes[0]} label={first?.name ?? menu.restaurantName} /></div>
    <div className="mt-auto px-5 pb-5 text-center">{first ? <p className="break-words font-impact text-xl uppercase leading-tight sm:text-2xl">{first.name}{first.price ? <span className="ml-3 text-poster">{first.price}</span> : null}</p> : null}{offers.length > 1 ? <p className="mt-1 break-words font-impact text-xs uppercase">{offers.slice(1).map((offer) => `${offer.name}${offer.price ? ` ${offer.price}` : ""}`).join("  •  ")}</p> : null}<p className="mt-2 font-mono text-[10px] font-bold uppercase">{[menu.footerLeft, menu.footerRight].filter(Boolean).join("  •  ")}</p></div>
  </div>;
}

function PosterSheet({ menu, size, dishes, aspect }: { menu: GeneratedMenu; size: OutputSize; dishes: Shot[]; aspect?: number | undefined }) {
  const offers = menu.sections.flatMap((section) => section.items.map((item) => ({ ...item, section: section.title })));
  const headlineSize = menu.tagline.length > 24 ? "text-[2.5rem] sm:text-6xl" : menu.tagline.length > 16 ? "text-5xl sm:text-7xl" : "text-6xl sm:text-8xl";
  const landscape = size === "wide" || (aspect !== undefined && aspect >= 1.2);
  return (
    <div className={`print-sheet poster-texture relative mx-auto w-full overflow-hidden bg-card text-ink shadow-2xl ${landscape ? "poster-landscape max-w-[920px]" : "max-w-[560px]"} ${aspect ? "sign-custom" : ""}`} style={aspect ? { aspectRatio: String(aspect) } : undefined}>
      <div className="bg-poster px-7 py-3 text-center font-impact text-sm uppercase text-poster-foreground">{menu.eyebrow}</div>
      <header className="relative border-b-[6px] border-ink bg-sun px-7 py-7 text-center">
        <p className="font-impact text-lg uppercase text-poster">{menu.restaurantName}</p>
        <h2 className={`mt-1 break-words font-impact uppercase leading-[0.82] text-ink ${headlineSize}`}>{menu.tagline}</h2>
        <span className="absolute -bottom-4 left-1/2 -translate-x-1/2 -rotate-2 bg-ink px-5 py-1.5 font-impact text-sm uppercase text-poster-foreground">Deals worth sharing</span>
      </header>

      <div className={`grid ${landscape ? "grid-cols-3" : "grid-cols-1"}`}>
        {offers.map((offer, index) => {
          const shot = dishes[index];
          return (
            <article key={`${offer.name}-${index}`} className={`relative grid min-h-[190px] overflow-hidden border-ink ${landscape ? "grid-rows-[1fr_auto] border-r-2" : "grid-cols-[44%_56%] border-b-2"}`}>
              <div className="relative min-h-[180px] overflow-hidden bg-line/40">
                {shot ? <img src={shot.src} alt={shot.label} width={1024} height={1024} loading={index === 0 ? "eager" : "lazy"} className={`absolute inset-0 h-full w-full object-cover transition-[filter] ${shot.final ? "blur-0" : "blur-xl"}`} /> : <div className="absolute inset-0 grid place-items-center"><Flame className="size-10 text-poster/35" /></div>}
                {offer.badge ? <span className="absolute left-3 top-3 max-w-[85%] -rotate-2 bg-sun px-2 py-1 font-impact text-[10px] uppercase text-ink shadow-[2px_2px_0_var(--ink)]">{offer.badge}</span> : null}
              </div>
              <div className="flex min-w-0 flex-col justify-center px-5 py-5">
                <p className="font-mono text-[9px] font-bold uppercase text-poster">{offer.section}</p>
                <h3 className={`mt-1 break-words font-impact uppercase leading-[0.9] ${offer.name.length > 24 ? "text-xl sm:text-3xl" : "text-2xl sm:text-4xl"}`}>{offer.name}</h3>
                <p className="mt-3 text-xs font-medium leading-snug text-muted">{offer.description}</p>
                <div className="mt-4 self-start bg-poster px-4 py-2 font-impact text-3xl leading-none text-poster-foreground shadow-[4px_4px_0_var(--ink)]">{offer.price}</div>
              </div>
            </article>
          );
        })}
      </div>

      {menu.features.length > 0 ? <footer className="grid grid-cols-3 divide-x-2 divide-ink bg-ink px-3 py-4 text-poster-foreground">
        {menu.features.slice(0, 3).map((feature, index) => {
          const Icon = index === 0 ? Sparkles : index === 1 ? Flame : Check;
          return <div key={feature} className="flex items-center justify-center gap-2 px-2 text-center font-impact text-[10px] uppercase"><Icon className="size-4 text-sun" />{feature}</div>;
        })}
      </footer> : null}
    </div>
  );
}

/** Print page size for a custom display, injected only when one is set. */
function CustomPageStyle({ display }: { display: DisplaySpec }) {
  return <style>{`@media print { .sign-custom { page: halftonecustom; width: ${display.widthCm}cm !important; height: ${display.heightCm}cm !important; max-width: none !important; } @page halftonecustom { size: ${display.widthCm}cm ${display.heightCm}cm; margin: 0; } }`}</style>;
}

export function MenuSheet({ menu, styleKey, size, hero, dishes = [], theme, display }: { menu: GeneratedMenu; styleKey: StyleKey; size: OutputSize; hero?: Shot | null; dishes?: Shot[]; theme?: SheetTheme | undefined; display?: DisplaySpec | undefined }) {
  const custom = display && display.format === "custom" ? display : undefined;
  const frame = (children: React.ReactNode) => <div style={theme as CSSProperties | undefined}>{custom ? <CustomPageStyle display={custom} /> : null}{children}</div>;

  if (custom && custom.orientation === "ultra-wide") return frame(<FasciaSheet menu={menu} size={custom.aspect >= 4.5 ? "fascia-447" : "fascia-180"} dishes={dishes} aspect={custom.aspect} />);
  if (custom && custom.orientation === "square") return frame(<SquareSign menu={menu} dishes={dishes} />);
  if (custom && (menu.mode === "poster" || styleKey === "bold-deals")) return frame(<PosterSheet menu={menu} size={custom.orientation === "landscape" ? "wide" : "poster"} dishes={dishes} aspect={custom.aspect} />);

  if (size === "fascia-447" || size === "fascia-180") return frame(<FasciaSheet menu={menu} size={size} dishes={dishes} />);
  if (size === "square") return frame(<SquareSign menu={menu} dishes={dishes} />);
  if (menu.mode === "poster" || styleKey === "bold-deals") return frame(<PosterSheet menu={menu} size={size} dishes={dishes} />);
  const s = styleClasses[styleKey];
  return frame(
    <div className="relative mx-auto w-full max-w-[460px] animate-proof">
      <div className="absolute inset-0 -z-10 translate-x-3 translate-y-4 rounded-[8px] bg-press/30 no-print" />
      <div className="print-sheet relative rounded-[8px] border-2 border-ink/15 bg-card px-9 py-10 text-ink">
        <div className="flex items-end justify-between font-mono text-[9px] font-bold uppercase text-muted"><span>Proof</span><span>{custom ? `${custom.widthCm} × ${custom.heightCm} CM` : size === "a5" ? "A5 · 148 × 210 MM" : "8.5 × 11 IN"}</span></div>
        <div className="mt-8 text-center animate-rise"><p className="font-mono text-[10px] font-bold uppercase text-press">{menu.eyebrow}</p><h2 className={`mt-2 ${s.name} ${s.title} leading-[0.95]`}>{menu.restaurantName}</h2>{menu.tagline ? <p className="mt-2 font-display text-base italic text-muted">{menu.tagline}</p> : null}</div>
        {hero ? <figure className="mt-6 overflow-hidden rounded-[4px] border border-ink/15"><img src={hero.src} alt={hero.label} width={1024} height={1024} className={`h-[150px] w-full object-cover transition-[filter] ${hero.final ? "blur-0" : "blur-xl"}`} /></figure> : null}
        <div className="mt-8 h-[3px] bg-press" />
        {menu.sections.map((section, i) => <section key={`${section.title}-${i}`} className="mt-7 animate-rise"><div className="flex items-baseline justify-between gap-3"><h3 className={`${s.section} text-[11px] uppercase text-press`}>{section.title}</h3>{section.note ? <span className="font-mono text-[10px] text-muted">{section.note}</span> : null}</div><div className="mt-3 space-y-3">{section.items.map((item, j) => <div key={`${item.name}-${j}`}><div className="flex items-baseline gap-2"><span className={`${s.item} text-lg`}>{item.name}</span><span className="flex-1 border-b-2 border-dotted border-ink/25" /><span className="font-mono text-sm font-bold text-press-deep">{item.price}</span></div>{item.description ? <p className="text-[13px] leading-snug text-muted">{item.description}</p> : null}</div>)}</div></section>)}
        {dishes.length > 0 ? <div className="mt-8 grid grid-cols-3 gap-2">{dishes.map((shot, i) => <figure key={`${shot.label}-${i}`}><img src={shot.src} alt={shot.label} width={1024} height={1024} loading="lazy" className={`aspect-square w-full rounded-[3px] object-cover ${shot.final ? "blur-0" : "blur-lg"}`} /><figcaption className="mt-1 font-mono text-[8px] font-bold uppercase text-muted">{shot.label}</figcaption></figure>)}</div> : null}
        <div className="mt-9 flex justify-between border-t-2 border-line pt-4 font-mono text-[10px] font-bold uppercase text-muted"><span>{menu.footerLeft}</span><span className="text-herb">{menu.footerRight}</span></div>
      </div>
    </div>,
  );
}

```

===== FILE: src/components/StoreWizard.tsx =====
```tsx
import { useNavigate } from "@tanstack/react-router";
import { useServerFn } from "@tanstack/react-start";
import { Camera, Check, LoaderCircle, Ruler, Sparkles, Store, Upload, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { STORE_TYPE_GUIDE } from "@/lib/ai/store";
import { analyzeStore, measureDisplay, type AnalyzeStoreResult, type MeasureDisplayResult, type TemplateMatch } from "@/lib/store.functions";
import { saveSetup } from "@/lib/setup";

const ALLOWED = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_BYTES = 8 * 1024 * 1024;

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => (typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("The image could not be read.")));
    reader.onerror = () => reject(new Error("The image could not be read."));
    reader.readAsDataURL(file);
  });
}

function imageAspect(dataUrl: string) {
  return new Promise<number>((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image.naturalHeight ? image.naturalWidth / image.naturalHeight : 4 / 3);
    image.onerror = () => resolve(4 / 3);
    image.src = dataUrl;
  });
}

function Label({ children }: { children: React.ReactNode }) {
  return <p className="mt-5 font-mono text-[10px] font-bold uppercase text-muted">{children}</p>;
}

function Swatch({ hex }: { hex: string }) {
  return <span className="inline-block size-4 rounded-sm border border-line" style={{ backgroundColor: hex }} title={hex} />;
}

function TemplateCard({ match, selected, onSelect }: { match: TemplateMatch; selected: boolean; onSelect: () => void }) {
  const theme = match.template.theme as Record<string, string>;
  return (
    <button type="button" onClick={onSelect} className={`group grid w-full grid-cols-[72px_minmax(0,1fr)] gap-3 border-2 p-3 text-left transition-colors ${selected ? "border-press bg-card" : "border-line bg-card/60 hover:border-ink/40"}`}>
      <div className="grid h-[72px] grid-rows-[1fr_2fr] overflow-hidden rounded-sm border border-line" style={{ background: theme["--paper"] ?? "var(--paper)" }}>
        <div style={{ background: theme["--poster"] ?? theme["--press"] ?? "var(--poster)" }} />
        <div className="grid grid-cols-2 gap-1 p-1">
          <div className="rounded-sm" style={{ background: theme["--sun"] ?? theme["--press"] ?? "var(--sun)" }} />
          <div className="rounded-sm" style={{ background: theme["--ink"] ?? "var(--ink)" }} />
        </div>
      </div>
      <div className="min-w-0">
        <div className="flex items-center justify-between gap-2">
          <p className="truncate font-display text-base font-black">{match.template.name}</p>
          {selected ? <Check className="size-4 shrink-0 text-press" /> : <span className="font-mono text-[9px] font-bold uppercase text-muted">{match.score}</span>}
        </div>
        <p className="text-[11px] leading-snug text-muted">{match.template.tagline}</p>
        <ul className="mt-1 space-y-0.5">{match.reasons.map((reason) => <li key={reason} className="text-[10px] leading-snug text-herb">✓ {reason}</li>)}</ul>
      </div>
    </button>
  );
}

/**
 * Three steps: analyse the store → pick a template → measure the display and
 * paste the current menu. Saves the result for the studio and navigates there.
 */
export function StoreWizard() {
  const navigate = useNavigate();
  const runAnalyze = useServerFn(analyzeStore);
  const runMeasure = useServerFn(measureDisplay);

  const [description, setDescription] = useState("");
  const [venueName, setVenueName] = useState("");
  const [storePhotos, setStorePhotos] = useState<{ file: File; url: string }[]>([]);
  const [analysis, setAnalysis] = useState<AnalyzeStoreResult | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [templateId, setTemplateId] = useState<string | null>(null);

  const [menuText, setMenuText] = useState("");
  const [displayPhoto, setDisplayPhoto] = useState<{ file: File; url: string } | null>(null);
  const [widthCm, setWidthCm] = useState("");
  const [heightCm, setHeightCm] = useState("");
  const [distanceM, setDistanceM] = useState("");
  const [hint, setHint] = useState("");
  const [measurement, setMeasurement] = useState<MeasureDisplayResult | null>(null);
  const [measuring, setMeasuring] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function addPhotos(files: FileList | null, target: "store" | "display") {
    if (!files) return;
    setError(null);
    for (const file of Array.from(files).slice(0, target === "store" ? 4 : 1)) {
      if (!ALLOWED.has(file.type)) { setError("Upload JPG, PNG or WebP photos."); continue; }
      if (file.size > MAX_BYTES) { setError("Photos must be smaller than 8 MB."); continue; }
      const url = URL.createObjectURL(file);
      if (target === "store") setStorePhotos((previous) => [...previous, { file, url }].slice(0, 4));
      else { if (displayPhoto) URL.revokeObjectURL(displayPhoto.url); setDisplayPhoto({ file, url }); setMeasurement(null); }
    }
  }

  async function analyse() {
    if (description.trim().length < 10 || analyzing) return;
    setAnalyzing(true); setError(null); setAnalysis(null); setTemplateId(null);
    try {
      const images = await Promise.all(storePhotos.map((photo) => fileToDataUrl(photo.file)));
      const result = await runAnalyze({ data: { description, ...(venueName.trim() ? { venueName: venueName.trim() } : {}), ...(images.length ? { images } : {}) } });
      setAnalysis(result);
      setTemplateId(result.templates[0]?.template.id ?? null);
      if (!venueName && result.profile.venueName) setVenueName(result.profile.venueName);
    } catch (caught) { setError((caught as Error).message); } finally { setAnalyzing(false); }
  }

  async function measure() {
    if (!displayPhoto || measuring) return;
    setMeasuring(true); setError(null);
    try {
      const image = await fileToDataUrl(displayPhoto.file);
      const aspect = await imageAspect(image);
      const result = await runMeasure({ data: {
        image,
        imageAspect: aspect,
        ...(Number(widthCm) > 0 ? { widthCm: Number(widthCm) } : {}),
        ...(Number(heightCm) > 0 ? { heightCm: Number(heightCm) } : {}),
        ...(Number(distanceM) > 0 ? { viewingDistanceM: Number(distanceM) } : {}),
        ...(analysis?.storeId ? { storeId: analysis.storeId } : {}),
        ...(hint.trim() ? { hint: hint.trim() } : {}),
      } });
      setMeasurement(result);
    } catch (caught) { setError((caught as Error).message); } finally { setMeasuring(false); }
  }

  function finish() {
    if (!analysis || !templateId) return;
    const match = analysis.templates.find((candidate) => candidate.template.id === templateId);
    if (!match) return;
    saveSetup({
      savedAt: new Date().toISOString(),
      storeId: analysis.storeId,
      displayId: measurement?.displayId,
      venueName: venueName || analysis.profile.venueName,
      profile: analysis.profile,
      template: { id: match.template.id, name: match.template.name, style: match.template.style, mode: match.template.mode, theme: match.template.theme, tagline: match.template.tagline },
      display: measurement?.spec,
      menuText: menuText.trim() || undefined,
    });
    void navigate({ to: "/" });
  }

  const guide = analysis ? STORE_TYPE_GUIDE[analysis.profile.storeType] : null;
  const spec = measurement?.spec;

  return (
    <div className="mx-auto max-w-5xl px-5 py-8 lg:px-10">
      <p className="font-mono text-[10px] font-bold uppercase text-press">Store setup</p>
      <h1 className="mt-2 max-w-[22ch] font-display text-4xl font-black leading-none">Tell Halftone about the store, pick a look, fit the display.</h1>
      <p className="mt-3 max-w-xl text-sm leading-relaxed text-muted">Three quick steps. The studio then designs for your real display size and your brand, and only keeps what customers can read from where they stand.</p>

      <div className="mt-8 grid gap-8 lg:grid-cols-2">
        <section className="border-t-2 border-ink pt-5">
          <div className="flex items-center gap-2"><Store className="size-4 text-press" /><h2 className="font-display text-xl font-black">1 · Analyse the store</h2></div>
          <Label>Venue name</Label>
          <input value={venueName} onChange={(event) => setVenueName(event.target.value)} placeholder="Yiros House" className="mt-2 w-full rounded-md border-2 border-line bg-card p-3 text-sm outline-none focus:border-press" />
          <Label>Describe the store</Label>
          <textarea rows={6} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Greek takeaway on a busy high street, counter service, mostly families and tradies at lunch, yellow and black branding, a wide board above the counter and a window poster. Known for lamb yiros and family packs." className="mt-2 w-full resize-y rounded-md border-2 border-line bg-card p-3 text-[13px] leading-relaxed outline-none focus:border-press" />
          <Label>Photos of the storefront, interior or current signage (up to 4)</Label>
          <div className="mt-2 flex flex-wrap gap-2">
            {storePhotos.map((photo, index) => (
              <div key={photo.url} className="relative"><img src={photo.url} alt={`Store photo ${index + 1}`} className="size-20 rounded-sm object-cover" /><button type="button" aria-label="Remove photo" onClick={() => { URL.revokeObjectURL(photo.url); setStorePhotos((previous) => previous.filter((_, i) => i !== index)); }} className="absolute -right-1 -top-1 grid size-5 place-items-center rounded-full bg-ink text-card"><X className="size-3" /></button></div>
            ))}
            {storePhotos.length < 4 ? <Button asChild variant="outline" className="h-20 w-20 border-dashed"><label><Upload className="size-4" /><input type="file" multiple accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={(event) => { void addPhotos(event.target.files, "store"); event.target.value = ""; }} /></label></Button> : null}
          </div>
          <Button onClick={analyse} disabled={analyzing || description.trim().length < 10} className="mt-5 h-11 w-full bg-ink text-card hover:bg-press">{analyzing ? <LoaderCircle className="animate-spin" /> : <Sparkles />}{analyzing ? "Analysing the store…" : "Analyse and suggest templates"}</Button>

          {analysis ? (
            <div className="mt-5 border-l-4 border-herb bg-card p-4">
              <p className="font-mono text-[10px] font-bold uppercase text-herb">Store profile · confidence {Math.round(analysis.profile.confidence * 100)}%</p>
              <p className="mt-1 text-sm font-semibold">{guide?.label} · {analysis.profile.priceTier} · {analysis.profile.orderFlow.replace("-", " ")}{analysis.profile.cuisine ? ` · ${analysis.profile.cuisine}` : ""}</p>
              {analysis.profile.palette.length ? <div className="mt-2 flex items-center gap-1">{analysis.profile.palette.slice(0, 5).map((hex) => <Swatch key={hex} hex={hex} />)}<span className="ml-1 font-mono text-[9px] uppercase text-muted">brand colours</span></div> : null}
              <p className="mt-2 text-[11px] leading-snug text-muted">{analysis.profile.notes}</p>
              <p className="mt-2 text-[11px] leading-snug text-muted"><span className="font-semibold text-ink">Copy voice:</span> {guide?.voice}</p>
              {analysis.warnings.map((warning) => <p key={warning} className="mt-2 text-[11px] text-press">{warning}</p>)}
            </div>
          ) : null}
        </section>

        <section className="border-t-2 border-ink pt-5">
          <div className="flex items-center gap-2"><Sparkles className="size-4 text-press" /><h2 className="font-display text-xl font-black">2 · Choose a template</h2></div>
          {analysis ? (
            <div className="mt-4 grid gap-3">{analysis.templates.map((match) => <TemplateCard key={match.template.id} match={match} selected={templateId === match.template.id} onSelect={() => setTemplateId(match.template.id)} />)}</div>
          ) : <p className="mt-4 text-sm text-muted">Templates appear here after the analysis, ranked for your store type, price point, cuisine, light and display surfaces.</p>}
        </section>

        <section className="border-t-2 border-ink pt-5 lg:col-span-2">
          <div className="flex items-center gap-2"><Ruler className="size-4 text-press" /><h2 className="font-display text-xl font-black">3 · Your current menu and the display</h2></div>
          <div className="mt-4 grid gap-6 lg:grid-cols-2">
            <div>
              <Label>Current menu (paste names and prices, or attach it in the studio)</Label>
              <textarea rows={10} value={menuText} onChange={(event) => setMenuText(event.target.value)} placeholder={"Lamb Yiros $16.90\nChicken Yiros $15.90\nFamily Pack — 5 yiros and family chips $69.90"} className="mt-2 w-full resize-y rounded-md border-2 border-line bg-card p-3 font-mono text-[12px] leading-relaxed outline-none focus:border-press" />
            </div>
            <div>
              <Label>Photo of the display (board, screen, window, A-frame)</Label>
              {displayPhoto ? <div className="relative mt-2 inline-block"><img src={displayPhoto.url} alt="Display" className="max-h-44 rounded-sm object-cover" /><button type="button" aria-label="Remove display photo" onClick={() => { URL.revokeObjectURL(displayPhoto.url); setDisplayPhoto(null); setMeasurement(null); }} className="absolute -right-1 -top-1 grid size-5 place-items-center rounded-full bg-ink text-card"><X className="size-3" /></button></div>
                : <Button asChild variant="outline" className="mt-2 w-full border-dashed border-press text-press"><label><Camera />Attach display photo<input type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={(event) => { void addPhotos(event.target.files, "display"); event.target.value = ""; }} /></label></Button>}
              <p className="mt-2 text-[11px] leading-snug text-muted">Include something of known size in the shot if you can (a door, an A4 sheet, the counter). Or type the real size below; one dimension is enough.</p>
              <div className="mt-3 grid grid-cols-3 gap-2">
                <input inputMode="decimal" value={widthCm} onChange={(event) => setWidthCm(event.target.value)} placeholder="Width cm" className="rounded-md border-2 border-line bg-card p-2 text-sm outline-none focus:border-press" />
                <input inputMode="decimal" value={heightCm} onChange={(event) => setHeightCm(event.target.value)} placeholder="Height cm" className="rounded-md border-2 border-line bg-card p-2 text-sm outline-none focus:border-press" />
                <input inputMode="decimal" value={distanceM} onChange={(event) => setDistanceM(event.target.value)} placeholder="Read from m" className="rounded-md border-2 border-line bg-card p-2 text-sm outline-none focus:border-press" />
              </div>
              <input value={hint} onChange={(event) => setHint(event.target.value)} placeholder="Optional hint: the door on the left is a standard 2.04 m door" className="mt-2 w-full rounded-md border-2 border-line bg-card p-2 text-sm outline-none focus:border-press" />
              <Button onClick={measure} disabled={!displayPhoto || measuring} variant="outline" className="mt-3 w-full border-press text-press">{measuring ? <LoaderCircle className="animate-spin" /> : <Ruler />}{measuring ? "Measuring…" : "Measure the display"}</Button>
              {spec ? (
                <div className="mt-4 border-l-4 border-herb bg-card p-4">
                  <p className="font-mono text-[10px] font-bold uppercase text-herb">Display · {spec.displayType.replace("-", " ")} · {spec.sizeSource === "user" ? "your size" : spec.sizeSource === "model" ? `estimated, ${Math.round(spec.sizeConfidence * 100)}% confidence` : "shape only"}</p>
                  <p className="mt-1 text-sm font-semibold">{spec.widthCm} × {spec.heightCm} cm · {spec.orientation} · {spec.formatLabel}</p>
                  <p className="mt-1 text-[11px] leading-snug text-muted">Read from about {spec.viewingDistanceM} m in {spec.lighting} light → names need ≥ {spec.budget.minLetterHeightCm} cm letters. Fits up to {spec.budget.maxItems} items ({spec.budget.maxOffers} photo offers), names ≤ {spec.budget.maxNameWords} words, descriptions ≤ {spec.budget.maxDescriptionWords} words.</p>
                  {spec.notes.map((note) => <p key={note} className="mt-1 text-[11px] text-muted">{note}</p>)}
                  {measurement?.warnings.map((warning) => <p key={warning} className="mt-1 text-[11px] text-press">{warning}</p>)}
                </div>
              ) : null}
            </div>
          </div>
        </section>
      </div>

      {error ? <p role="alert" className="mt-6 border-l-4 border-destructive bg-card p-3 text-xs font-semibold text-destructive">{error}</p> : null}

      <div className="mt-8 flex flex-wrap items-center gap-3 border-t-2 border-line pt-5">
        <Button onClick={finish} disabled={!analysis || !templateId} className="h-11 bg-poster text-poster-foreground hover:bg-press-deep"><Check />Use this setup in the studio</Button>
        <p className="text-[11px] text-muted">{!analysis ? "Analyse the store first." : !measurement ? "You can skip the display measurement; the studio will use a standard format." : "Ready."}</p>
      </div>
    </div>
  );
}

```

===== FILE: src/routes/setup.tsx =====
```tsx
import { createFileRoute, Link } from "@tanstack/react-router";

import { StoreWizard } from "@/components/StoreWizard";

export const Route = createFileRoute("/setup")({
  head: () => ({
    meta: [
      { title: "Halftone — Store setup" },
      { name: "description", content: "Analyse your store, pick a template and fit the menu to your real display." },
    ],
  }),
  component: SetupPage,
});

function SetupPage() {
  return (
    <div className="min-h-screen bg-paper font-body text-ink antialiased">
      <header className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b-2 border-ink/15 px-5 py-4 lg:px-10">
        <div className="flex min-w-0 items-center gap-3"><span className="grid size-9 shrink-0 place-items-center bg-poster font-impact text-lg text-poster-foreground">H</span><div className="min-w-0"><span className="font-display text-xl font-black">Halftone</span><span className="ml-2 font-mono text-[10px] font-bold uppercase text-muted">store setup</span></div></div>
        <Link to="/" className="font-mono text-[10px] font-bold uppercase text-press">Back to studio</Link>
      </header>
      <StoreWizard />
    </div>
  );
}

```

===== FILE: src/routes/index.tsx =====
```tsx
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { useServerFn } from "@tanstack/react-start";
import { Camera, FileDown, ImagePlus, LoaderCircle, Sparkles, Store, Upload, WandSparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import familyGyros from "@/assets/sample-family-gyros.jpg";
import gyros from "@/assets/sample-gyros.jpg";
import seafood from "@/assets/sample-seafood.jpg";
import { BriefCoach } from "@/components/BriefCoach";
import { EnginePanel } from "@/components/EnginePanel";
import { MenuSheet, type OutputSize, type Shot, type StyleKey } from "@/components/MenuSheet";
import { Button } from "@/components/ui/button";
import { generateMenu, stripEngine, type GeneratedMenu } from "@/lib/menu.functions";
import { briefFromSetup, clearSetup, loadSetup, rendererSizeFor, storeContext, type StudioSetup } from "@/lib/setup";
import { streamImage } from "@/lib/streamImage";

export const Route = createFileRoute("/")({
  head: () => ({ meta: [
    { title: "Halftone — Professional Menu & Deal Poster Design" },
    { name: "description", content: "Create professional restaurant menus and photo-led deal posters from a plain-language brief, with exact prices and print-ready layouts." },
    { property: "og:title", content: "Halftone — Professional Menu Design" },
    { property: "og:description", content: "Turn a restaurant brief into a polished menu or promotional deal poster with commercial food photography." },
    { property: "og:type", content: "website" }, { name: "twitter:card", content: "summary_large_image" },
  ] }), component: Studio,
});

const SAMPLE: GeneratedMenu = {
  mode: "poster", restaurantName: "Yiros House", tagline: "Family Feast Deals", eyebrow: "Fresh from the grill · this week only",
  sections: [{ title: "SHARING DEALS", note: null, items: [
    { name: "Yiros Meal Deal", description: "3 lamb yiros loaded with salad and tzatziki, plus a large serve of chips.", price: "$49.90", badge: "FEEDS 3", photoPrompt: "Three complete lamb gyros and one large basket of chips on a dark round board." },
    { name: "Seafood Family Pack", description: "3 battered fish, calamari, grilled prawns, chips, lemon and tartare sauce.", price: "$59.90", badge: "FAMILY FAVOURITE", photoPrompt: "Complete seafood family platter with three fish, calamari, prawns, chips, lemon and tartare." },
    { name: "Big Family Yiros", description: "5 chicken yiros with salad and garlic sauce, plus a family-size serve of chips.", price: "$69.90", badge: "BIG VALUE", photoPrompt: "Five complete chicken gyros and a family basket of chips on a dark round board." },
  ] }],
  footerLeft: "ORDER ONLINE", footerRight: "PICKUP · DELIVERY", followed: [], features: ["Fresh ingredients", "Cooked to order", "Big portions"],
  heroPhoto: { label: "Family feast", prompt: "A generous family table filled with gyros and sides." },
  dishPhotos: [
    { label: "Yiros Meal Deal", prompt: "Three complete lamb gyros and a large basket of chips." },
    { label: "Seafood Family Pack", prompt: "A complete seafood family platter." },
    { label: "Big Family Yiros", prompt: "Five chicken gyros and a family basket of chips." },
  ],
};
const SAMPLE_SHOTS: Shot[] = [
  { label: "Yiros Meal Deal", src: gyros, final: true }, { label: "Seafood Family Pack", src: seafood, final: true }, { label: "Big Family Yiros", src: familyGyros, final: true },
];
const CLASSIC_SAMPLE: GeneratedMenu = {
  mode: "menu",
  restaurantName: "Yiros House",
  tagline: "Fresh from the grill",
  eyebrow: "Lunch & dinner",
  sections: [
    { title: "Yiros", note: "Wrapped fresh", items: [
      { name: "Lamb Yiros", description: "Tomato, onion, parsley and tzatziki", price: "$16.90", badge: "", photoPrompt: "A freshly wrapped lamb yiros" },
      { name: "Chicken Yiros", description: "Lettuce, tomato and garlic sauce", price: "$15.90", badge: "", photoPrompt: "A freshly wrapped chicken yiros" },
    ] },
    { title: "From the grill", note: null, items: [
      { name: "Mixed Grill", description: "Lamb, chicken, pita, salad and tzatziki", price: "$27.90", badge: "", photoPrompt: "A complete mixed grill plate" },
      { name: "Halloumi Plate", description: "Grilled halloumi, pita and village salad", price: "$22.90", badge: "", photoPrompt: "A grilled halloumi plate with salad" },
    ] },
    { title: "Sides", note: null, items: [
      { name: "Seasoned Chips", description: "Oregano salt", price: "$8.90", badge: "", photoPrompt: "A bowl of seasoned chips" },
      { name: "Village Salad", description: "Tomato, cucumber, olives and feta", price: "$11.90", badge: "", photoPrompt: "A Greek village salad" },
    ] },
  ],
  footerLeft: "DINE IN · TAKEAWAY",
  footerRight: "ORDER AT THE COUNTER",
  followed: [],
  features: ["Fresh ingredients", "Cooked to order", "House-made sauces"],
  heroPhoto: { label: "Mixed grill", prompt: "A complete Greek mixed grill plate on a restaurant table" },
  dishPhotos: [
    { label: "Lamb Yiros", prompt: "A freshly wrapped lamb yiros" },
    { label: "Mixed Grill", prompt: "A complete Greek mixed grill plate" },
    { label: "Village Salad", prompt: "A Greek village salad" },
  ],
};

function Choice({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <Button type="button" variant={active ? "default" : "outline"} size="sm" onClick={onClick} className={active ? "bg-press text-card hover:bg-press-deep" : ""}>{children}</Button>;
}

const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

async function validateImage(file: File) {
  if (!ALLOWED_IMAGE_TYPES.has(file.type)) throw new Error("Upload a JPG, PNG or WebP image.");
  if (file.size > MAX_IMAGE_BYTES) throw new Error("Images must be smaller than 8 MB.");
  const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  const png = bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
  const jpeg = bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  const webp = String.fromCharCode(...bytes.slice(0, 4)) === "RIFF" && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP";
  if (!png && !jpeg && !webp) throw new Error("That file is not a valid JPG, PNG or WebP image.");
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("The image could not be read."));
    reader.onerror = () => reject(new Error("The image could not be read."));
    reader.readAsDataURL(file);
  });
}

function Studio() {
  const [brief, setBrief] = useState("Create a bold family deals poster for Yiros House. Include: Yiros Meal Deal — 3 lamb yiros and large chips $49.90; Seafood Family Pack — 3 fish, calamari, prawns and chips $59.90; Big Family Yiros — 5 chicken yiros and family chips $69.90. Use the exact names and prices.");
  const [mode, setMode] = useState<"menu" | "poster">("poster");
  const [style, setStyle] = useState<StyleKey>("bold-deals");
  const [pages, setPages] = useState<"1" | "2">("1");
  const [size, setSize] = useState<OutputSize>("poster");
  const [menu, setMenu] = useState<GeneratedMenu>(SAMPLE);
  const [hero, setHero] = useState<Shot | null>(null);
  const [dishes, setDishes] = useState<Shot[]>(SAMPLE_SHOTS);
  const [shooting, setShooting] = useState(false);
  const [photoProgress, setPhotoProgress] = useState(0);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [menuReference, setMenuReference] = useState<File | null>(null);
  const [menuReferenceUrl, setMenuReferenceUrl] = useState<string | null>(null);
  const [foodReference, setFoodReference] = useState<File | null>(null);
  const [foodReferenceUrl, setFoodReferenceUrl] = useState<string | null>(null);
  const [photoInstruction, setPhotoInstruction] = useState("Make this dish look like premium restaurant campaign photography while keeping the same food and ingredients.");
  const [photoTarget, setPhotoTarget] = useState(0);
  const [enhancing, setEnhancing] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [setup, setSetup] = useState<StudioSetup | null>(null);
  const artworkRef = useRef<HTMLElement>(null);
  const run = useServerFn(generateMenu);

  // Pick up the store wizard's result (template, display, store profile, current menu).
  useEffect(() => {
    const saved = loadSetup();
    if (!saved) return;
    setSetup(saved);
    setMode(saved.template.mode);
    setStyle(saved.template.style);
    setSize(rendererSizeFor(saved.display, saved.template.mode));
    setBrief(briefFromSetup(saved));
    setMenu(saved.template.mode === "poster" ? SAMPLE : CLASSIC_SAMPLE);
    setDishes(saved.template.mode === "poster" ? SAMPLE_SHOTS : []);
  }, []);

  const mutation = useMutation({
    mutationFn: async (revise: boolean) => run({ data: {
      brief, mode, style, pages, size,
      ...(revise ? { previous: JSON.stringify(stripEngine(menu)) } : {}),
      ...(menuReference ? { referenceImage: await fileToDataUrl(menuReference) } : {}),
      ...(setup ? { template: setup.template.id, store: storeContext(setup.profile), ...(setup.display ? { display: setup.display } : {}), ...(setup.storeId ? { storeId: setup.storeId } : {}), ...(setup.displayId ? { displayId: setup.displayId } : {}) } : {}),
    } }),
    onSuccess: (result) => {
      setMenu(result); setHero(null); setDishes([]); setPhotoError(null);
      window.setTimeout(() => artworkRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
    },
  });

  function chooseMode(next: "menu" | "poster") {
    setMode(next); setStyle(next === "poster" ? "bold-deals" : "editorial"); setSize(next === "poster" ? "poster" : "letter");
    if (next === "poster") { setMenu(SAMPLE); setDishes(SAMPLE_SHOTS); setBrief("Create a bold family deals poster for Yiros House. Include: Yiros Meal Deal — 3 lamb yiros and large chips $49.90; Seafood Family Pack — 3 fish, calamari, prawns and chips $59.90; Big Family Yiros — 5 chicken yiros and family chips $69.90. Use the exact names and prices."); }
    else { setMenu(CLASSIC_SAMPLE); setDishes([]); setBrief("Create a one-page menu for Yiros House with sections for yiros, grill plates and sides. Keep the writing concise and use dollar prices."); }
  }

  function dropSetup() {
    clearSetup(); setSetup(null); chooseMode(mode);
  }

  async function chooseUpload(file: File | undefined, purpose: "menu" | "food") {
    if (!file) return;
    setUploadError(null);
    try {
      await validateImage(file);
      const url = URL.createObjectURL(file);
      if (purpose === "menu") {
        if (menuReferenceUrl) URL.revokeObjectURL(menuReferenceUrl);
        setMenuReference(file); setMenuReferenceUrl(url);
      } else {
        if (foodReferenceUrl) URL.revokeObjectURL(foodReferenceUrl);
        setFoodReference(file); setFoodReferenceUrl(url);
      }
    } catch (error) { setUploadError((error as Error).message); }
  }

  function removeUpload(purpose: "menu" | "food") {
    if (purpose === "menu") {
      if (menuReferenceUrl) URL.revokeObjectURL(menuReferenceUrl);
      setMenuReference(null); setMenuReferenceUrl(null);
    } else {
      if (foodReferenceUrl) URL.revokeObjectURL(foodReferenceUrl);
      setFoodReference(null); setFoodReferenceUrl(null);
    }
  }

  async function enhanceFoodPhoto() {
    if (!foodReference || enhancing) return;
    setEnhancing(true); setPhotoError(null);
    const targets = menu.mode === "poster" ? menu.sections.flatMap((section) => section.items) : menu.dishPhotos;
    const target = targets[photoTarget] ?? targets[0];
    const label = target && "name" in target ? target.name : target?.label ?? foodReference.name;
    const menuPrompt = target && "photoPrompt" in target ? target.photoPrompt : target?.prompt ?? "";
    const form = new FormData();
    form.set("image", foodReference, foodReference.name);
    form.set("prompt", `${photoInstruction}\n\nThis source photo is the actual dish and is authoritative. Preserve the same dish identity, ingredients, portion logic, cookware and recognisable food details. Do not add or remove menu items. Improve only the plating neatness, appetising texture, true-to-life colour, controlled softbox lighting, natural shadows, camera framing and clean restaurant-advertising finish. ${menuPrompt} Square composition, complete dish visible. No text, logo, watermark, illustration, CGI or plastic-looking food.`);
    try {
      await streamImage(form, (src, final) => setDishes((previous) => {
        const next = [...previous]; next[photoTarget] = { label, src, final }; return next;
      }));
    } catch (error) { setPhotoError((error as Error).message); } finally { setEnhancing(false); }
  }

  async function shootPhotography() {
    if (shooting) return;
    setShooting(true); setPhotoProgress(0); setPhotoError(null); setHero(null); setDishes([]);
    try {
      if (menu.mode === "menu" && menu.heroPhoto) await streamImage(`${menu.heroPhoto.prompt} Wide banner crop.`, (src, final) => setHero({ label: menu.heroPhoto?.label ?? menu.restaurantName, src, final }));
      const items = menu.sections.flatMap((section) => section.items);
      const shots = (menu.mode === "poster" ? items.map((item) => ({ label: item.name, prompt: item.photoPrompt })) : (menu.dishPhotos ?? [])).slice(0, menu.mode === "poster" ? 4 : 3);
      for (let index = 0; index < shots.length; index++) { const shot = shots[index]; if (!shot) continue; setPhotoProgress(index + 1); await streamImage(`${shot.prompt} Square composition with the complete advertised meal fully visible and clean edges.`, (src, final) => setDishes((previous) => { const next = [...previous]; next[index] = { label: shot.label, src, final }; return next; })); }
    } catch (error) { setPhotoError((error as Error).message); } finally { setShooting(false); }
  }

  const artworkLabel = setup?.display ? `${setup.display.widthCm} × ${setup.display.heightCm} cm` : size === "fascia-447" ? "447 × 71.5 cm" : size === "fascia-180" ? "180 × 60.4 cm" : size === "square" ? "12 × 12 in" : size.replace("poster", "portrait");

  return <div className="min-h-screen bg-paper font-body text-ink antialiased">
    <header className="no-print grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b-2 border-ink/15 px-5 py-4 lg:px-10"><div className="flex min-w-0 items-center gap-3"><span className="grid size-9 shrink-0 place-items-center bg-poster font-impact text-lg text-poster-foreground">H</span><div className="min-w-0"><span className="font-display text-xl font-black">Halftone</span><span className="ml-2 font-mono text-[10px] font-bold uppercase text-muted">menu studio</span></div></div><div className="flex items-center gap-4"><Link to="/setup" className="font-mono text-[10px] font-bold uppercase text-press">Store setup</Link><span className="hidden font-mono text-[10px] font-bold uppercase text-press sm:block">● Print ready</span></div></header>
    <main className="print-shell grid min-h-[calc(100vh-70px)] lg:grid-cols-[minmax(340px,4fr)_minmax(0,8fr)]">
      <aside className="no-print border-line px-5 py-7 lg:border-r lg:px-8">
        {setup ? <section className="mb-6 border-l-4 border-ink bg-card p-4"><div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2"><Store className="size-4 text-ink" /><p className="font-mono text-[10px] font-bold uppercase text-ink">Store setup active</p></div><button type="button" onClick={dropSetup} className="font-mono text-[9px] font-bold uppercase text-muted hover:text-press">Clear</button></div><p className="mt-2 text-xs font-semibold">{setup.venueName || setup.profile.venueName || "Your venue"} · {setup.template.name}</p><p className="mt-1 text-[11px] leading-snug text-muted">{setup.display ? `${setup.display.displayType.replace("-", " ")} ${setup.display.widthCm} × ${setup.display.heightCm} cm, read from ${setup.display.viewingDistanceM} m · fits ${setup.display.budget.maxItems} items / ${setup.display.budget.maxOffers} offers` : "No display measured; standard format."} · <Link to="/setup" className="text-press">change</Link></p></section> : null}
        <p className="font-mono text-[10px] font-bold uppercase text-press">Creative brief</p><h1 className="mt-2 max-w-[18ch] font-display text-4xl font-black leading-none">Build the menu people remember.</h1><p className="mt-3 max-w-md text-sm leading-relaxed text-muted">Give exact offers, prices and brand direction. Halftone composes the words itself so every important detail stays correct.</p>
        <div className="mt-6 grid grid-cols-2 gap-2"><Choice active={mode === "poster"} onClick={() => chooseMode("poster")}>Bold deals</Choice><Choice active={mode === "menu"} onClick={() => chooseMode("menu")}>Classic menu</Choice></div>
        <label htmlFor="brief" className="mt-6 block font-mono text-[10px] font-bold uppercase text-muted">Your brief</label><textarea id="brief" rows={8} maxLength={4000} value={brief} onChange={(event) => setBrief(event.target.value)} className="mt-2 w-full resize-y rounded-md border-2 border-line bg-card p-4 text-[13px] leading-relaxed outline-none transition-colors focus:border-press" />
        <BriefCoach brief={brief} mode={mode} />
        <section className="mt-5 border-y-2 border-line py-5"><div className="flex items-center gap-2"><ImagePlus className="size-4 text-press"/><p className="font-mono text-[10px] font-bold uppercase text-muted">Redesign an existing menu</p></div><p className="mt-2 text-xs leading-relaxed text-muted">Attach a clear menu photo or screenshot. Your written brief controls corrections.</p>{menuReference && menuReferenceUrl ? <div className="mt-3 grid grid-cols-[64px_minmax(0,1fr)_auto] items-center gap-3 bg-card p-2"><img src={menuReferenceUrl} alt="Menu reference preview" className="aspect-square w-16 object-cover"/><div className="min-w-0"><p className="truncate text-xs font-semibold">{menuReference.name}</p><p className="mt-1 font-mono text-[9px] uppercase text-herb">Ready to analyse</p></div><Button type="button" variant="ghost" size="icon" aria-label="Remove menu image" onClick={() => removeUpload("menu")}><X/></Button></div> : <Button asChild variant="outline" className="mt-3 w-full border-dashed border-press text-press"><label><Upload/>Attach menu image<input type="file" className="sr-only" accept="image/jpeg,image/png,image/webp" onChange={(event) => { void chooseUpload(event.target.files?.[0], "menu"); event.target.value = ""; }}/></label></Button>}</section>
        <p className="mt-5 font-mono text-[10px] font-bold uppercase text-muted">Format{setup?.display?.format === "custom" ? " · custom display size from setup" : ""}</p><div className="mt-2 flex flex-wrap gap-2">{mode === "poster" ? <><Choice active={size === "poster"} onClick={() => setSize("poster")}>Portrait poster</Choice><Choice active={size === "wide"} onClick={() => setSize("wide")}>Wide board</Choice><Choice active={size === "fascia-447"} onClick={() => setSize("fascia-447")}>Fascia · 447 × 71.5 cm</Choice><Choice active={size === "fascia-180"} onClick={() => setSize("fascia-180")}>Board · 180 × 60.4 cm</Choice><Choice active={size === "square"} onClick={() => setSize("square")}>Pizza sign · 12 in</Choice></> : <><Choice active={size === "letter"} onClick={() => setSize("letter")}>Letter</Choice><Choice active={size === "a5"} onClick={() => setSize("a5")}>A5</Choice><Choice active={pages === "1"} onClick={() => setPages("1")}>1 page</Choice><Choice active={pages === "2"} onClick={() => setPages("2")}>2 pages</Choice></>}</div>
        <div className="mt-7 grid gap-2"><Button onClick={() => mutation.mutate(false)} disabled={mutation.isPending || shooting || enhancing || brief.trim().length < 3} className="h-11 bg-ink text-card hover:bg-press">{mutation.isPending ? <LoaderCircle className="animate-spin" /> : <Sparkles />}{mutation.isPending ? (menuReference ? "Reading and redesigning with every engine…" : "Composing with every engine…") : menuReference ? "Redesign from image" : "Generate design"}</Button><div className="grid grid-cols-2 gap-2"><Button variant="outline" onClick={() => mutation.mutate(true)} disabled={mutation.isPending || shooting || enhancing}>Revise design</Button><Button variant="outline" onClick={shootPhotography} disabled={shooting || enhancing || mutation.isPending} className="border-press text-press">{shooting ? <LoaderCircle className="animate-spin" /> : <Camera />}{shooting ? `Photo ${photoProgress || 1}…` : "Create photos"}</Button></div></div>
        <section className="mt-6 bg-card p-4"><div className="flex items-center gap-2"><WandSparkles className="size-4 text-herb"/><p className="font-mono text-[10px] font-bold uppercase text-herb">Improve your food photo</p></div><p className="mt-2 text-xs leading-relaxed text-muted">Keep the real dish, then improve its lighting, plating and campaign finish.</p>{foodReference && foodReferenceUrl ? <div className="mt-3 grid grid-cols-[64px_minmax(0,1fr)_auto] items-center gap-3 border border-line p-2"><img src={foodReferenceUrl} alt="Food photo preview" className="aspect-square w-16 object-cover"/><p className="min-w-0 truncate text-xs font-semibold">{foodReference.name}</p><Button type="button" variant="ghost" size="icon" aria-label="Remove food photo" onClick={() => removeUpload("food")}><X/></Button></div> : <Button asChild variant="outline" className="mt-3 w-full border-dashed"><label><Upload/>Attach food photo<input type="file" className="sr-only" accept="image/jpeg,image/png,image/webp" onChange={(event) => { void chooseUpload(event.target.files?.[0], "food"); event.target.value = ""; }}/></label></Button>}{foodReference ? <><label htmlFor="photo-instruction" className="mt-3 block font-mono text-[9px] font-bold uppercase text-muted">What should improve?</label><textarea id="photo-instruction" rows={3} value={photoInstruction} onChange={(event) => setPhotoInstruction(event.target.value)} className="mt-1 w-full resize-y rounded-md border border-line bg-paper p-3 text-xs outline-none focus:border-herb"/><p className="mt-3 font-mono text-[9px] font-bold uppercase text-muted">Place it in</p><div className="mt-2 flex flex-wrap gap-2">{(menu.mode === "poster" ? menu.sections.flatMap((section) => section.items.map((item) => item.name)) : menu.dishPhotos.map((item) => item.label)).slice(0, 4).map((label, index) => <Choice key={`${label}-${index}`} active={photoTarget === index} onClick={() => setPhotoTarget(index)}>{label}</Choice>)}</div><Button type="button" onClick={enhanceFoodPhoto} disabled={enhancing || shooting || mutation.isPending || photoInstruction.trim().length < 3} className="mt-3 w-full bg-herb text-card hover:bg-ink">{enhancing ? <LoaderCircle className="animate-spin"/> : <WandSparkles/>}{enhancing ? "Improving photo…" : "Improve and place photo"}</Button></> : null}</section>
        {mutation.isError || photoError || uploadError ? <p role="alert" className="mt-4 border-l-4 border-destructive bg-card p-3 text-xs font-semibold text-destructive">{uploadError ?? photoError ?? (mutation.error as Error).message}</p> : null}
        {menu.followed.length > 0 ? <section className="mt-6 border-l-4 border-herb bg-card p-4"><p className="font-mono text-[10px] font-bold uppercase text-herb">Instructions followed</p><ul className="mt-2 space-y-1">{menu.followed.map((item) => <li key={item} className="text-xs text-muted">✓ {item}</li>)}</ul></section> : null}
        <EnginePanel engine={menu.engine} />
      </aside>
      <section ref={artworkRef} className="scroll-mt-4 min-w-0 bg-ink/[0.04] px-3 py-5 sm:px-5 lg:px-10"><div className="no-print mx-auto mb-5 grid max-w-[1100px] grid-cols-[minmax(0,1fr)_auto] items-center gap-3"><div className="min-w-0"><p className="truncate font-mono text-[10px] font-bold uppercase text-muted">Live artwork · {artworkLabel}{setup ? ` · ${setup.template.name}` : ""}</p>{shooting ? <p className="mt-1 text-xs font-semibold text-press">Creating professional photo {photoProgress || 1}…</p> : null}</div><Button onClick={() => window.print()} className="shrink-0 bg-poster text-poster-foreground hover:bg-press-deep"><FileDown />Print / save PDF</Button></div><MenuSheet menu={menu} styleKey={style} size={size} hero={hero} dishes={dishes} theme={setup?.template.theme as Record<string, string> | undefined} display={setup?.display} /></section>
    </main>
  </div>;
}

```

===== FILE: src/routeTree.gen.ts =====
```tsx
/* eslint-disable */

// @ts-nocheck

// noinspection JSUnusedGlobalSymbols

// This file was automatically generated by TanStack Router.
// You should NOT make any changes in this file as it will be overwritten.
// Additionally, you should also exclude this file from your linter and/or formatter to prevent it from being checked or modified.

import { Route as rootRouteImport } from './routes/__root'
import { Route as SetupRouteImport } from './routes/setup'
import { Route as IndexRouteImport } from './routes/index'
import { Route as ApiEditImageRouteImport } from './routes/api/edit-image'
import { Route as ApiGenerateImageRouteImport } from './routes/api/generate-image'

const SetupRoute = SetupRouteImport.update({
  id: '/setup',
  path: '/setup',
  getParentRoute: () => rootRouteImport,
} as any)
const IndexRoute = IndexRouteImport.update({
  id: '/',
  path: '/',
  getParentRoute: () => rootRouteImport,
} as any)
const ApiEditImageRoute = ApiEditImageRouteImport.update({
  id: '/api/edit-image',
  path: '/api/edit-image',
  getParentRoute: () => rootRouteImport,
} as any)
const ApiGenerateImageRoute = ApiGenerateImageRouteImport.update({
  id: '/api/generate-image',
  path: '/api/generate-image',
  getParentRoute: () => rootRouteImport,
} as any)

export interface FileRoutesByFullPath {
  '/': typeof IndexRoute
  '/setup': typeof SetupRoute
  '/api/edit-image': typeof ApiEditImageRoute
  '/api/generate-image': typeof ApiGenerateImageRoute
}
export interface FileRoutesByTo {
  '/': typeof IndexRoute
  '/setup': typeof SetupRoute
  '/api/edit-image': typeof ApiEditImageRoute
  '/api/generate-image': typeof ApiGenerateImageRoute
}
export interface FileRoutesById {
  __root__: typeof rootRouteImport
  '/': typeof IndexRoute
  '/setup': typeof SetupRoute
  '/api/edit-image': typeof ApiEditImageRoute
  '/api/generate-image': typeof ApiGenerateImageRoute
}
export interface FileRouteTypes {
  fileRoutesByFullPath: FileRoutesByFullPath
  fullPaths: '/' | '/setup' | '/api/edit-image' | '/api/generate-image'
  fileRoutesByTo: FileRoutesByTo
  to: '/' | '/setup' | '/api/edit-image' | '/api/generate-image'
  id: '__root__' | '/' | '/setup' | '/api/edit-image' | '/api/generate-image'
  fileRoutesById: FileRoutesById
}
export interface RootRouteChildren {
  IndexRoute: typeof IndexRoute
  SetupRoute: typeof SetupRoute
  ApiEditImageRoute: typeof ApiEditImageRoute
  ApiGenerateImageRoute: typeof ApiGenerateImageRoute
}

declare module '@tanstack/react-router' {
  interface FileRoutesByPath {
    '/': {
      id: '/'
      path: '/'
      fullPath: '/'
      preLoaderRoute: typeof IndexRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/setup': {
      id: '/setup'
      path: '/setup'
      fullPath: '/setup'
      preLoaderRoute: typeof SetupRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/api/edit-image': {
      id: '/api/edit-image'
      path: '/api/edit-image'
      fullPath: '/api/edit-image'
      preLoaderRoute: typeof ApiEditImageRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/api/generate-image': {
      id: '/api/generate-image'
      path: '/api/generate-image'
      fullPath: '/api/generate-image'
      preLoaderRoute: typeof ApiGenerateImageRouteImport
      parentRoute: typeof rootRouteImport
    }
  }
}

const rootRouteChildren: RootRouteChildren = {
  IndexRoute: IndexRoute,
  SetupRoute: SetupRoute,
  ApiEditImageRoute: ApiEditImageRoute,
  ApiGenerateImageRoute: ApiGenerateImageRoute,
}
export const routeTree = rootRouteImport
  ._addFileChildren(rootRouteChildren)
  ._addFileTypes<FileRouteTypes>()

import type { getRouter } from './router.tsx'
import type { startInstance } from './start.ts'
declare module '@tanstack/react-start' {
  interface Register {
    ssr: true
    router: Awaited<ReturnType<typeof getRouter>>
    config: Awaited<ReturnType<typeof startInstance.getOptions>>
  }
}

```
