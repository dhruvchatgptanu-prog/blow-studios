import { Link } from "@tanstack/react-router";
import { ArrowUp, ImagePlus, LoaderCircle, Paperclip, Store, UtensilsCrossed, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from "react";

import type { ChatSettings } from "@/lib/chat";
import { imagesFromTransfer } from "@/lib/images";
import type { StudioSetup } from "@/lib/setup";

export type Attachments = { menus: File[]; food?: File | undefined };
export const MAX_MENU_PHOTOS = 8;

function Chip({ active, onClick, children, title }: { active?: boolean; onClick?: () => void; children: React.ReactNode; title?: string }) {
  return (
    <button type="button" title={title} onClick={onClick} className={`rounded-full border px-3 py-1 font-mono text-[10px] font-bold uppercase transition-colors ${active ? "border-ink bg-ink text-card" : "border-line bg-card text-muted hover:border-ink hover:text-ink"}`}>
      {children}
    </button>
  );
}

/**
 * The message box at the bottom of the chat: free text, photos of the current
 * menu (several at once, by button, drag-and-drop or paste), one food photo to
 * improve, and two chips. Size, style and template come from the words.
 */
export function ChatComposer({ value, onChange, onSend, busy, attachments, onAttach, onRemove, settings, onSettings, setup, onClearSetup, error }: {
  value: string;
  onChange: (next: string) => void;
  onSend: () => void;
  busy: boolean;
  attachments: Attachments;
  onAttach: (kind: "menu" | "food", files: File[]) => void;
  onRemove: (kind: "menu" | "food", index?: number) => void;
  settings: ChatSettings;
  onSettings: (next: Partial<ChatSettings>) => void;
  setup: StudioSetup | null;
  onClearSetup: () => void;
  error: string | null;
}) {
  const [attachOpen, setAttachOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const area = areaRef.current;
    if (!area) return;
    area.style.height = "0px";
    area.style.height = `${Math.min(220, Math.max(52, area.scrollHeight))}px`;
  }, [value]);

  const previews = useMemo(() => attachments.menus.map((file) => URL.createObjectURL(file)), [attachments.menus]);
  useEffect(() => () => previews.forEach((url) => URL.revokeObjectURL(url)), [previews]);
  const foodPreview = useMemo(() => (attachments.food ? URL.createObjectURL(attachments.food) : null), [attachments.food]);
  useEffect(() => () => { if (foodPreview) URL.revokeObjectURL(foodPreview); }, [foodPreview]);

  const hasAttachment = attachments.menus.length > 0 || Boolean(attachments.food);
  const canSend = !busy && (value.trim().length > 0 || hasAttachment);

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSend) onSend();
    }
  }
  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = imagesFromTransfer(event.clipboardData);
    if (files.length) { event.preventDefault(); onAttach("menu", files); }
  }
  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false);
    const files = imagesFromTransfer(event.dataTransfer);
    if (files.length) onAttach("menu", files);
  }

  return (
    <div className="no-print mx-auto w-full max-w-3xl px-3 pb-4 sm:px-5">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <Chip active={settings.mode === "poster"} onClick={() => onSettings({ mode: "poster", style: "bold-deals", size: "poster" })}>Bold deals</Chip>
        <Chip active={settings.mode === "menu"} onClick={() => onSettings({ mode: "menu", style: "editorial", size: "letter" })}>Classic menu</Chip>
        <Chip active={settings.autoPhotos !== false} onClick={() => onSettings({ autoPhotos: settings.autoPhotos === false })} title="Create the food photos automatically after every new design">Auto photos {settings.autoPhotos !== false ? "on" : "off"}</Chip>
        <span className="mx-1 h-4 w-px bg-line" />
        {setup ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-herb bg-card px-3 py-1 font-mono text-[10px] font-bold uppercase text-herb">
            <Store className="size-3" />{setup.venueName || setup.profile.venueName || "Store"} · {setup.template.name}
            <button type="button" aria-label="Clear store setup" onClick={onClearSetup} className="ml-1 text-muted hover:text-press"><X className="size-3" /></button>
          </span>
        ) : (
          <Link to="/setup" className="inline-flex items-center gap-1 rounded-full border border-line bg-card px-3 py-1 font-mono text-[10px] font-bold uppercase text-muted hover:border-ink hover:text-ink"><Store className="size-3" />Set up my store</Link>
        )}
        {settings.display ? <span className="rounded-full border border-line bg-card px-3 py-1 font-mono text-[10px] font-bold uppercase text-muted">{settings.display.widthCm} × {settings.display.heightCm} cm</span> : null}
        {settings.look ? <span className="rounded-full border border-line bg-card px-3 py-1 font-mono text-[10px] font-bold uppercase text-muted">Look · {settings.look.name}</span> : null}
      </div>

      {hasAttachment ? (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          {attachments.menus.map((file, index) => (
            <span key={`${file.name}-${index}`} className="relative">
              <img src={previews[index]} alt={file.name} className="size-14 rounded-lg border border-line object-cover" />
              <span className="absolute bottom-0 left-0 rounded-tr-md bg-ink/80 px-1 font-mono text-[8px] uppercase text-card">page {index + 1}</span>
              <button type="button" aria-label={`Remove ${file.name}`} onClick={() => onRemove("menu", index)} className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full bg-ink text-card hover:bg-press"><X className="size-3" /></button>
            </span>
          ))}
          {attachments.menus.length > 0 ? <span className="font-mono text-[9px] font-bold uppercase text-muted">{attachments.menus.length} menu photo{attachments.menus.length === 1 ? "" : "s"} to redesign</span> : null}
          {attachments.food && foodPreview ? (
            <span className="relative">
              <img src={foodPreview} alt={attachments.food.name} className="size-14 rounded-lg border-2 border-herb object-cover" />
              <span className="absolute bottom-0 left-0 rounded-tr-md bg-herb px-1 font-mono text-[8px] uppercase text-card">food</span>
              <button type="button" aria-label="Remove food photo" onClick={() => onRemove("food")} className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full bg-ink text-card hover:bg-press"><X className="size-3" /></button>
            </span>
          ) : null}
        </div>
      ) : null}

      <div
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`relative rounded-3xl border-2 bg-card shadow-sm transition-colors focus-within:border-ink ${dragging ? "border-press bg-paper" : "border-line"}`}
      >
        <textarea
          ref={areaRef}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKey}
          onPaste={onPaste}
          rows={1}
          maxLength={4000}
          placeholder={attachments.food ? "What should improve in this photo, and which dish is it?" : attachments.menus.length ? "Anything to change in the redesign? Add the real size, e.g. '120 x 60 cm board'." : "Describe the menu or poster: venue, dishes, exact prices, and the real size (e.g. 120 x 60 cm board, A4, 55-inch TV)…"}
          className="block w-full resize-none bg-transparent py-4 pl-14 pr-14 text-[14px] leading-relaxed text-ink outline-none placeholder:text-muted"
        />
        <div className="absolute bottom-2.5 left-2.5">
          <button type="button" aria-label="Attach photos" onClick={() => setAttachOpen((open) => !open)} className="grid size-9 place-items-center rounded-full text-muted hover:bg-paper hover:text-ink"><Paperclip className="size-4" /></button>
          {attachOpen ? (
            <div className="absolute bottom-11 left-0 w-72 rounded-xl border border-line bg-card p-1 shadow-lg">
              <label className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs hover:bg-paper"><ImagePlus className="size-4 text-press" /><span>Photos of my current menu<span className="block font-mono text-[9px] uppercase text-muted">all pages at once, up to {MAX_MENU_PHOTOS}</span></span><input type="file" multiple className="sr-only" accept="image/jpeg,image/png,image/webp" onChange={(event) => { onAttach("menu", Array.from(event.target.files ?? [])); event.target.value = ""; setAttachOpen(false); }} /></label>
              <label className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs hover:bg-paper"><UtensilsCrossed className="size-4 text-herb" /><span>A food photo to improve<span className="block font-mono text-[9px] uppercase text-muted">kept as the real dish, restyled</span></span><input type="file" className="sr-only" accept="image/jpeg,image/png,image/webp" onChange={(event) => { onAttach("food", Array.from(event.target.files ?? [])); event.target.value = ""; setAttachOpen(false); }} /></label>
            </div>
          ) : null}
        </div>
        <button type="button" aria-label="Send" onClick={onSend} disabled={!canSend} className="absolute bottom-2.5 right-2.5 grid size-9 place-items-center rounded-full bg-ink text-card transition-colors hover:bg-press disabled:opacity-30">
          {busy ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
        </button>
      </div>
      {error ? <p role="alert" className="mt-2 text-xs font-semibold text-destructive">{error}</p> : null}
      <p className="mt-2 text-center font-mono text-[9px] uppercase text-muted">Drop or paste menu photos · say the size in words and it is fitted at the end · Enter to send</p>
    </div>
  );
}
