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
