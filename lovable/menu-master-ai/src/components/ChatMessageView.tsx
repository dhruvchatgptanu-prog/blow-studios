import { Camera, FileDown, LoaderCircle, Palette } from "lucide-react";

import { EnginePanel } from "@/components/EnginePanel";
import { MenuSheet, type Shot } from "@/components/MenuSheet";
import type { ChatMessage } from "@/lib/chat";
import type { StudioSetup } from "@/lib/setup";

export type MessageShots = { hero: Shot | null; dishes: Shot[]; progress: number; busy: boolean; error?: string | undefined };

function Avatar() {
  return <span className="grid size-8 shrink-0 place-items-center rounded-full bg-poster font-impact text-sm text-poster-foreground">H</span>;
}

/** One turn of the conversation. Assistant turns that carry a design render it full size with its actions. */
export function ChatMessageView({ message, shots, previews, setup, printing, onPhotos, onPrint, onRestyle, busy }: {
  message: ChatMessage;
  shots: MessageShots | undefined;
  /** Local object URLs for this session's uploads, before storage returns signed URLs. */
  previews: string[] | undefined;
  setup: StudioSetup | null;
  printing: boolean;
  onPhotos: (messageId: string) => void;
  onPrint: (messageId: string) => void;
  onRestyle: (messageId: string) => void;
  busy: boolean;
}) {
  if (message.role === "user") {
    const images = (message.attachments ?? []).map((attachment, index) => ({ attachment, url: attachment.asset?.url || previews?.[index] }));
    return (
      <div className="no-print flex justify-end">
        <div className="max-w-[85%] rounded-3xl rounded-br-md bg-ink px-4 py-3 text-[14px] leading-relaxed text-card">
          {images.length ? (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {images.map(({ attachment, url }, index) => url ? (
                <img key={`${attachment.name}-${index}`} src={url} alt={attachment.name} className={`size-16 rounded-lg object-cover ${attachment.kind === "food" ? "border-2 border-herb" : "border border-card/30"}`} />
              ) : (
                <span key={`${attachment.name}-${index}`} className="rounded-full bg-card/15 px-2 py-0.5 font-mono text-[9px] uppercase">{attachment.kind === "menu" ? "menu photo" : "food photo"} · {attachment.name}</span>
              ))}
            </div>
          ) : null}
          <p className="whitespace-pre-wrap">{message.text}</p>
        </div>
      </div>
    );
  }

  const menu = message.menu;
  const render = message.render;
  const theme = (render?.look?.theme ?? (setup?.template.theme as Record<string, string> | undefined));
  const display = render?.display ?? setup?.display;
  return (
    <div className="flex items-start gap-3">
      <span className="no-print"><Avatar /></span>
      <div className="min-w-0 flex-1">
        {message.status === "thinking" ? (
          <p className="no-print inline-flex items-center gap-2 text-[14px] text-muted"><LoaderCircle className="size-4 animate-spin" />{message.text || "Composing with every engine…"}</p>
        ) : (
          <p className={`whitespace-pre-wrap text-[14px] leading-relaxed ${message.status === "error" ? "text-destructive" : "text-ink"} no-print`}>{message.text}</p>
        )}
        {menu && render ? (
          <div className="mt-4">
            {/* Only the sheet being printed keeps a print class; everything else on the page carries no-print. */}
            <div className={`rounded-2xl border border-line bg-ink/[0.04] p-3 sm:p-5 ${printing ? "print-shell" : "no-print"}`}>
              <MenuSheet menu={menu} styleKey={render.style} size={render.size} hero={shots?.hero ?? null} dishes={shots?.dishes ?? []} theme={theme} display={display} />
            </div>
            <div className="no-print mt-3 flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => onPhotos(message.id)} disabled={busy || shots?.busy} className="inline-flex items-center gap-1.5 rounded-full border border-press bg-card px-3 py-1.5 font-mono text-[10px] font-bold uppercase text-press hover:bg-press hover:text-card disabled:opacity-40">
                {shots?.busy ? <LoaderCircle className="size-3.5 animate-spin" /> : <Camera className="size-3.5" />}{shots?.busy ? `Photo ${shots.progress || 1}…` : shots?.dishes.length ? "Recreate photos" : "Create photos"}
              </button>
              <button type="button" onClick={() => onRestyle(message.id)} disabled={busy} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-card px-3 py-1.5 font-mono text-[10px] font-bold uppercase text-ink hover:border-ink disabled:opacity-40"><Palette className="size-3.5" />Another look</button>
              <button type="button" onClick={() => onPrint(message.id)} className="inline-flex items-center gap-1.5 rounded-full bg-poster px-3 py-1.5 font-mono text-[10px] font-bold uppercase text-poster-foreground hover:bg-press-deep"><FileDown className="size-3.5" />Print / save PDF</button>
              {render.look ? <span className="font-mono text-[9px] font-bold uppercase text-muted">{render.look.name}{display ? ` · ${display.widthCm} × ${display.heightCm} cm` : ""}</span> : null}
              {shots?.error ? <span className="text-xs font-semibold text-destructive">{shots.error}</span> : null}
            </div>
            <details className="no-print mt-2">
              <summary className="cursor-pointer font-mono text-[9px] font-bold uppercase text-muted hover:text-ink">What the engines did</summary>
              <div className="mt-2"><EnginePanel engine={menu.engine} /></div>
            </details>
          </div>
        ) : null}
      </div>
    </div>
  );
}
