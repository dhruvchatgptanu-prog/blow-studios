import { Camera, FileDown, ImagePlus, LoaderCircle, UtensilsCrossed } from "lucide-react";

import { EnginePanel } from "@/components/EnginePanel";
import { MenuSheet, type Shot } from "@/components/MenuSheet";
import type { ChatMessage } from "@/lib/chat";
import type { StudioSetup } from "@/lib/setup";

export type MessageShots = { hero: Shot | null; dishes: Shot[]; progress: number; busy: boolean; error?: string | undefined };

function Avatar() {
  return <span className="grid size-8 shrink-0 place-items-center rounded-full bg-poster font-impact text-sm text-poster-foreground">H</span>;
}

/** One turn of the conversation. Assistant turns that carry a design render it full size with its actions. */
export function ChatMessageView({ message, shots, setup, printing, onPhotos, onPrint, busy }: {
  message: ChatMessage;
  shots: MessageShots | undefined;
  setup: StudioSetup | null;
  printing: boolean;
  onPhotos: (messageId: string) => void;
  onPrint: (messageId: string) => void;
  busy: boolean;
}) {
  if (message.role === "user") {
    return (
      <div className="no-print flex justify-end">
        <div className="max-w-[85%] rounded-3xl rounded-br-md bg-ink px-4 py-3 text-[14px] leading-relaxed text-card">
          {message.attachments?.length ? (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {message.attachments.map((attachment) => (
                <span key={`${attachment.kind}-${attachment.name}`} className="inline-flex items-center gap-1 rounded-full bg-card/15 px-2 py-0.5 font-mono text-[9px] uppercase">
                  {attachment.kind === "menu" ? <ImagePlus className="size-3" /> : <UtensilsCrossed className="size-3" />}{attachment.kind === "menu" ? "menu photo" : "food photo"} · {attachment.name}
                </span>
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
              <MenuSheet menu={menu} styleKey={render.style} size={render.size} hero={shots?.hero ?? null} dishes={shots?.dishes ?? []} theme={setup?.template.theme as Record<string, string> | undefined} display={setup?.display} />
            </div>
            <div className="no-print mt-3 flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => onPhotos(message.id)} disabled={busy || shots?.busy} className="inline-flex items-center gap-1.5 rounded-full border border-press bg-card px-3 py-1.5 font-mono text-[10px] font-bold uppercase text-press hover:bg-press hover:text-card disabled:opacity-40">
                {shots?.busy ? <LoaderCircle className="size-3.5 animate-spin" /> : <Camera className="size-3.5" />}{shots?.busy ? `Photo ${shots.progress || 1}…` : shots?.dishes.length ? "Recreate photos" : "Create photos"}
              </button>
              <button type="button" onClick={() => onPrint(message.id)} className="inline-flex items-center gap-1.5 rounded-full bg-poster px-3 py-1.5 font-mono text-[10px] font-bold uppercase text-poster-foreground hover:bg-press-deep"><FileDown className="size-3.5" />Print / save PDF</button>
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
