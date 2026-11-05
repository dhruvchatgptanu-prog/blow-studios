import { Link } from "@tanstack/react-router";
import { ArrowUp, ImagePlus, LoaderCircle, Paperclip, Store, UtensilsCrossed, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { ChatSettings } from "@/lib/chat";
import type { StudioSetup } from "@/lib/setup";

export type Attachments = { menu?: File | undefined; food?: File | undefined };

const POSTER_SIZES: { key: ChatSettings["size"]; label: string }[] = [
  { key: "poster", label: "Portrait poster" },
  { key: "wide", label: "Wide board" },
  { key: "fascia-447", label: "Fascia 447 cm" },
  { key: "fascia-180", label: "Board 180 cm" },
  { key: "square", label: "Pizza sign" },
];
const MENU_SIZES: { key: ChatSettings["size"]; label: string }[] = [
  { key: "letter", label: "Letter" },
  { key: "a5", label: "A5" },
];

function Chip({ active, onClick, children, title }: { active?: boolean; onClick?: () => void; children: React.ReactNode; title?: string }) {
  return (
    <button type="button" title={title} onClick={onClick} className={`rounded-full border px-3 py-1 font-mono text-[10px] font-bold uppercase transition-colors ${active ? "border-ink bg-ink text-card" : "border-line bg-card text-muted hover:border-ink hover:text-ink"}`}>
      {children}
    </button>
  );
}

/**
 * The message box at the bottom of the chat: free text, two kinds of photo
 * attachment, and quick chips for mode and format (the words in the message
 * override the chips).
 */
export function ChatComposer({ value, onChange, onSend, busy, attachments, onAttach, onRemove, settings, onSettings, setup, onClearSetup, error }: {
  value: string;
  onChange: (next: string) => void;
  onSend: () => void;
  busy: boolean;
  attachments: Attachments;
  onAttach: (kind: "menu" | "food", file: File | undefined) => void;
  onRemove: (kind: "menu" | "food") => void;
  settings: ChatSettings;
  onSettings: (next: Partial<ChatSettings>) => void;
  setup: StudioSetup | null;
  onClearSetup: () => void;
  error: string | null;
}) {
  const [attachOpen, setAttachOpen] = useState(false);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const area = areaRef.current;
    if (!area) return;
    area.style.height = "0px";
    area.style.height = `${Math.min(220, Math.max(52, area.scrollHeight))}px`;
  }, [value]);

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!busy && (value.trim().length > 0 || attachments.food || attachments.menu)) onSend();
    }
  }

  const sizes = settings.mode === "poster" ? POSTER_SIZES : MENU_SIZES;
  const canSend = !busy && (value.trim().length > 0 || Boolean(attachments.food) || Boolean(attachments.menu));

  return (
    <div className="no-print mx-auto w-full max-w-3xl px-3 pb-4 sm:px-5">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <Chip active={settings.mode === "poster"} onClick={() => onSettings({ mode: "poster", style: "bold-deals", size: "poster" })}>Bold deals</Chip>
        <Chip active={settings.mode === "menu"} onClick={() => onSettings({ mode: "menu", style: "editorial", size: "letter" })}>Classic menu</Chip>
        <span className="mx-1 h-4 w-px bg-line" />
        {sizes.map((size) => <Chip key={size.key} active={settings.size === size.key} onClick={() => onSettings({ size: size.key })}>{size.label}</Chip>)}
        {settings.mode === "menu" ? <Chip active={settings.pages === "2"} onClick={() => onSettings({ pages: settings.pages === "2" ? "1" : "2" })}>{settings.pages === "2" ? "2 pages" : "1 page"}</Chip> : null}
        <span className="mx-1 h-4 w-px bg-line" />
        {setup ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-herb bg-card px-3 py-1 font-mono text-[10px] font-bold uppercase text-herb">
            <Store className="size-3" />{setup.venueName || setup.profile.venueName || "Store"} · {setup.template.name}
            <button type="button" aria-label="Clear store setup" onClick={onClearSetup} className="ml-1 text-muted hover:text-press"><X className="size-3" /></button>
          </span>
        ) : (
          <Link to="/setup" className="inline-flex items-center gap-1 rounded-full border border-line bg-card px-3 py-1 font-mono text-[10px] font-bold uppercase text-muted hover:border-ink hover:text-ink"><Store className="size-3" />Set up my store</Link>
        )}
      </div>

      {attachments.menu || attachments.food ? (
        <div className="mb-2 flex flex-wrap gap-2">
          {attachments.menu ? <AttachmentPill icon={<ImagePlus className="size-3" />} label={`Menu to redesign · ${attachments.menu.name}`} onRemove={() => onRemove("menu")} /> : null}
          {attachments.food ? <AttachmentPill icon={<UtensilsCrossed className="size-3" />} label={`Food photo to improve · ${attachments.food.name}`} onRemove={() => onRemove("food")} /> : null}
        </div>
      ) : null}

      <div className="relative rounded-3xl border-2 border-line bg-card shadow-sm transition-colors focus-within:border-ink">
        <textarea
          ref={areaRef}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKey}
          rows={1}
          maxLength={4000}
          placeholder={attachments.food ? "What should improve in this photo, and which dish is it?" : attachments.menu ? "What should change in the redesign?" : "Describe the menu or poster: venue, dishes, exact prices…"}
          className="block w-full resize-none bg-transparent py-4 pl-14 pr-14 text-[14px] leading-relaxed text-ink outline-none placeholder:text-muted"
        />
        <div className="absolute bottom-2.5 left-2.5">
          <button type="button" aria-label="Attach a photo" onClick={() => setAttachOpen((open) => !open)} className="grid size-9 place-items-center rounded-full text-muted hover:bg-paper hover:text-ink"><Paperclip className="size-4" /></button>
          {attachOpen ? (
            <div className="absolute bottom-11 left-0 w-60 rounded-xl border border-line bg-card p-1 shadow-lg">
              <label className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs hover:bg-paper"><ImagePlus className="size-4 text-press" />Menu photo to redesign<input type="file" className="sr-only" accept="image/jpeg,image/png,image/webp" onChange={(event) => { onAttach("menu", event.target.files?.[0]); event.target.value = ""; setAttachOpen(false); }} /></label>
              <label className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs hover:bg-paper"><UtensilsCrossed className="size-4 text-herb" />Food photo to improve<input type="file" className="sr-only" accept="image/jpeg,image/png,image/webp" onChange={(event) => { onAttach("food", event.target.files?.[0]); event.target.value = ""; setAttachOpen(false); }} /></label>
            </div>
          ) : null}
        </div>
        <button type="button" aria-label="Send" onClick={onSend} disabled={!canSend} className="absolute bottom-2.5 right-2.5 grid size-9 place-items-center rounded-full bg-ink text-card transition-colors hover:bg-press disabled:opacity-30">
          {busy ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
        </button>
      </div>
      {error ? <p role="alert" className="mt-2 text-xs font-semibold text-destructive">{error}</p> : null}
      <p className="mt-2 text-center font-mono text-[9px] uppercase text-muted">Exact names and prices are kept as you type them · Enter to send, Shift+Enter for a new line</p>
    </div>
  );
}

function AttachmentPill({ icon, label, onRemove }: { icon: React.ReactNode; label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-card px-3 py-1 text-[11px] text-ink">
      {icon}<span className="truncate">{label}</span>
      <button type="button" aria-label="Remove attachment" onClick={onRemove} className="text-muted hover:text-press"><X className="size-3" /></button>
    </span>
  );
}
