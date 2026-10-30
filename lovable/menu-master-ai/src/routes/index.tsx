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
