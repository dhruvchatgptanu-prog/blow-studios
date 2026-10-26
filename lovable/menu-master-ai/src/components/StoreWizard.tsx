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
