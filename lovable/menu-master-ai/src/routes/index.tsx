import { createFileRoute, Link } from "@tanstack/react-router";
import { useServerFn } from "@tanstack/react-start";
import { MessageSquarePlus, PanelLeft, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChatComposer, type Attachments } from "@/components/ChatComposer";
import { ChatMessageView, type MessageShots } from "@/components/ChatMessageView";
import type { Shot } from "@/components/MenuSheet";
import { conversationTitle, describeResult, extendBrief, HELP_REPLY, parseIntent, STARTERS, type Intent } from "@/lib/ai/intent";
import { DEFAULT_SETTINGS, latestDesign, loadConversations, newConversation, saveConversations, uid, type ChatMessage, type ChatSettings, type Conversation } from "@/lib/chat";
import { fileToDataUrl, validateImage } from "@/lib/images";
import { generateMenu, stripEngine, type GeneratedMenu } from "@/lib/menu.functions";
import { clearSetup, loadSetup, rendererSizeFor, storeContext, type StudioSetup } from "@/lib/setup";
import { streamImage } from "@/lib/streamImage";

export const Route = createFileRoute("/")({
  head: () => ({ meta: [
    { title: "Halftone — Chat your menu into print" },
    { name: "description", content: "Describe your menu or deal poster in plain words, then refine it in conversation. Exact prices, real display sizes, print-ready." },
    { property: "og:title", content: "Halftone — Chat your menu into print" },
    { property: "og:description", content: "Conversational menu and poster design with exact prices and commercial food photography." },
    { property: "og:type", content: "website" }, { name: "twitter:card", content: "summary_large_image" },
  ] }), component: ChatStudio,
});

const DEFAULT_FOOD_INSTRUCTION = "Make this dish look like premium restaurant campaign photography while keeping the same food and ingredients.";

function ChatStudio() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Attachments>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shots, setShots] = useState<Record<string, MessageShots>>({});
  const [printingId, setPrintingId] = useState<string | null>(null);
  const [setup, setSetup] = useState<StudioSetup | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const run = useServerFn(generateMenu);

  // Load saved conversations and the store wizard result once in the browser.
  useEffect(() => {
    const saved = loadConversations();
    const wizard = loadSetup();
    setSetup(wizard);
    const settings: ChatSettings = wizard
      ? { mode: wizard.template.mode, style: wizard.template.style, size: rendererSizeFor(wizard.display, wizard.template.mode), pages: "1" }
      : DEFAULT_SETTINGS;
    const first = saved[0] ?? newConversation(settings);
    setConversations(saved.length ? saved : [first]);
    setActiveId(first.id);
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (hydrated) saveConversations(conversations);
  }, [conversations, hydrated]);

  const active = useMemo(() => conversations.find((conversation) => conversation.id === activeId) ?? conversations[0], [conversations, activeId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [active?.messages.length, busy]);

  const patchConversation = useCallback((id: string, patch: (conversation: Conversation) => Conversation) => {
    setConversations((previous) => previous.map((conversation) => (conversation.id === id ? patch(conversation) : conversation)));
  }, []);

  function appendMessage(id: string, message: ChatMessage, extra: Partial<Conversation> = {}) {
    patchConversation(id, (conversation) => ({ ...conversation, ...extra, updatedAt: new Date().toISOString(), messages: [...conversation.messages, message] }));
  }

  function replaceMessage(id: string, messageId: string, patch: Partial<ChatMessage>) {
    patchConversation(id, (conversation) => ({ ...conversation, updatedAt: new Date().toISOString(), messages: conversation.messages.map((message) => (message.id === messageId ? { ...message, ...patch } : message)) }));
  }

  function startConversation() {
    const fresh = newConversation(active?.settings ?? DEFAULT_SETTINGS);
    setConversations((previous) => [fresh, ...previous]);
    setActiveId(fresh.id);
    setDraft(""); setAttachments({}); setError(null); setSidebarOpen(false);
  }

  function deleteConversation(id: string) {
    setConversations((previous) => {
      const next = previous.filter((conversation) => conversation.id !== id);
      if (next.length === 0) { const fresh = newConversation(DEFAULT_SETTINGS); setActiveId(fresh.id); return [fresh]; }
      if (id === activeId) setActiveId(next[0]!.id);
      return next;
    });
  }

  async function attach(kind: "menu" | "food", file: File | undefined) {
    if (!file) return;
    try { await validateImage(file); setAttachments((previous) => ({ ...previous, [kind]: file })); setError(null); } catch (failure) { setError((failure as Error).message); }
  }

  function dropSetup() { clearSetup(); setSetup(null); }

  async function send() {
    if (!active || busy) return;
    const text = draft.trim();
    const conversationId = active.id;
    const current = latestDesign(active);
    const foodFile = attachments.food;
    const menuFile = attachments.menu;
    const userMessage: ChatMessage = {
      id: uid(), role: "user", at: new Date().toISOString(),
      text: text || (foodFile ? DEFAULT_FOOD_INSTRUCTION : "Redesign this menu."),
      attachments: [...(menuFile ? [{ kind: "menu" as const, name: menuFile.name }] : []), ...(foodFile ? [{ kind: "food" as const, name: foodFile.name }] : [])],
    };
    setDraft(""); setAttachments({}); setError(null);

    // A food photo improves the current design's photography; no engine call needed.
    if (foodFile && current) {
      appendMessage(conversationId, userMessage);
      await improvePhoto(conversationId, current.message.id, current.menu, foodFile, text || DEFAULT_FOOD_INSTRUCTION);
      return;
    }

    const intent = parseIntent(text || (menuFile ? "Redesign the attached menu." : ""), { hasMenu: Boolean(current) && !menuFile, mode: active.settings.mode, size: active.settings.size, pages: active.settings.pages, style: active.settings.style });
    const settings: ChatSettings = { mode: intent.mode, style: intent.style, size: intent.size, pages: intent.pages };
    const title = active.messages.length === 0 ? conversationTitle(text || menuFile?.name || "New design") : active.title;
    appendMessage(conversationId, userMessage, { settings, title });

    if (intent.kind === "help") { appendMessage(conversationId, { id: uid(), role: "assistant", at: new Date().toISOString(), text: HELP_REPLY, status: "done" }); return; }
    if (intent.kind === "photos" && current) { appendMessage(conversationId, { id: uid(), role: "assistant", at: new Date().toISOString(), text: "Creating the photos for the design above now.", status: "done" }); await shootPhotos(current.message.id, current.menu); return; }

    await compose(conversationId, intent, settings, current && intent.kind === "revise" ? current.menu : null, menuFile, active.brief);
  }

  async function compose(conversationId: string, intent: Intent, settings: ChatSettings, previous: GeneratedMenu | null, menuFile: File | undefined, runningBrief: string) {
    const brief = previous ? extendBrief(runningBrief || intent.brief, intent.brief) : intent.brief;
    const pendingId = uid();
    appendMessage(conversationId, { id: pendingId, role: "assistant", at: new Date().toISOString(), text: previous ? "Revising with every engine…" : menuFile ? "Reading your menu and redesigning with every engine…" : "Composing with every engine…", status: "thinking" }, { brief });
    setBusy(true);
    try {
      const result = await run({ data: {
        brief, mode: settings.mode, style: settings.style, pages: settings.pages, size: settings.size,
        ...(previous ? { previous: JSON.stringify(stripEngine(previous)) } : {}),
        ...(menuFile ? { referenceImage: await fileToDataUrl(menuFile) } : {}),
        ...(setup ? { template: setup.template.id, store: storeContext(setup.profile), ...(setup.display ? { display: setup.display } : {}), ...(setup.storeId ? { storeId: setup.storeId } : {}), ...(setup.displayId ? { displayId: setup.displayId } : {}) } : {}),
      } });
      replaceMessage(conversationId, pendingId, { text: describeResult(result, intent), menu: result, render: settings, status: "done" });
    } catch (failure) {
      replaceMessage(conversationId, pendingId, { text: `That did not work: ${(failure as Error).message}`, status: "error" });
    } finally {
      setBusy(false);
    }
  }

  function setShot(messageId: string, patch: (previous: MessageShots) => MessageShots) {
    setShots((all) => ({ ...all, [messageId]: patch(all[messageId] ?? { hero: null, dishes: [], progress: 0, busy: false }) }));
  }

  async function shootPhotos(messageId: string, menu: GeneratedMenu) {
    setShot(messageId, () => ({ hero: null, dishes: [], progress: 0, busy: true }));
    try {
      if (menu.mode === "menu" && menu.heroPhoto) await streamImage(`${menu.heroPhoto.prompt} Wide banner crop.`, (src, final) => setShot(messageId, (previous) => ({ ...previous, hero: { label: menu.heroPhoto.label || menu.restaurantName, src, final } })));
      const items = menu.sections.flatMap((section) => section.items);
      const list: { label: string; prompt: string }[] = (menu.mode === "poster" ? items.map((item) => ({ label: item.name, prompt: item.photoPrompt })) : menu.dishPhotos).slice(0, menu.mode === "poster" ? 4 : 3);
      for (let index = 0; index < list.length; index++) {
        const shot = list[index]!;
        setShot(messageId, (previous) => ({ ...previous, progress: index + 1 }));
        await streamImage(`${shot.prompt} Square composition with the complete advertised meal fully visible and clean edges.`, (src, final) => setShot(messageId, (previous) => { const dishes = [...previous.dishes]; dishes[index] = { label: shot.label, src, final }; return { ...previous, dishes }; }));
      }
      setShot(messageId, (previous) => ({ ...previous, busy: false }));
    } catch (failure) {
      setShot(messageId, (previous) => ({ ...previous, busy: false, error: (failure as Error).message }));
    }
  }

  async function improvePhoto(conversationId: string, messageId: string, menu: GeneratedMenu, file: File, instruction: string) {
    const targets = menu.mode === "poster" ? menu.sections.flatMap((section) => section.items.map((item) => ({ label: item.name, prompt: item.photoPrompt }))) : menu.dishPhotos;
    const lower = instruction.toLowerCase();
    let index = targets.findIndex((target) => lower.includes(target.label.toLowerCase()));
    if (index < 0) index = 0;
    const target = targets[index];
    const replyId = uid();
    appendMessage(conversationId, { id: replyId, role: "assistant", at: new Date().toISOString(), text: `Improving your photo and placing it as "${target?.label ?? "the first dish"}"…`, status: "thinking" });
    setShot(messageId, (previous) => ({ ...previous, busy: true }));
    const form = new FormData();
    form.set("image", file, file.name);
    form.set("prompt", `${instruction}\n\nThis source photo is the actual dish and is authoritative. Preserve the same dish identity, ingredients, portion logic, cookware and recognisable food details. Do not add or remove menu items. Improve only the plating neatness, appetising texture, true-to-life colour, controlled softbox lighting, natural shadows, camera framing and clean restaurant-advertising finish. ${target?.prompt ?? ""} Square composition, complete dish visible. No text, logo, watermark, illustration, CGI or plastic-looking food.`);
    try {
      await streamImage(form, (src, final) => setShot(messageId, (previous) => { const dishes = [...previous.dishes]; dishes[index] = { label: target?.label ?? file.name, src, final } satisfies Shot; return { ...previous, dishes }; }));
      setShot(messageId, (previous) => ({ ...previous, busy: false }));
      replaceMessage(conversationId, replyId, { text: `Placed the improved photo as "${target?.label ?? "the first dish"}" in the design above. Say which other dish to shoot, or "create the photos" for all of them.`, status: "done" });
    } catch (failure) {
      setShot(messageId, (previous) => ({ ...previous, busy: false }));
      replaceMessage(conversationId, replyId, { text: `The photo could not be improved: ${(failure as Error).message}`, status: "error" });
    }
  }

  function printMessage(messageId: string) {
    setPrintingId(messageId);
    window.setTimeout(() => { window.print(); window.setTimeout(() => setPrintingId(null), 500); }, 80);
  }

  const empty = !active || active.messages.length === 0;

  return (
    <div className="flex min-h-screen bg-paper font-body text-ink antialiased">
      {/* Sidebar: conversations */}
      <aside className={`no-print fixed inset-y-0 left-0 z-20 w-72 -translate-x-full border-r border-line bg-card transition-transform lg:static lg:translate-x-0 ${sidebarOpen ? "translate-x-0" : ""}`}>
        <div className="flex h-full flex-col p-3">
          <div className="flex items-center gap-2 px-2 py-2"><span className="grid size-8 place-items-center bg-poster font-impact text-base text-poster-foreground">H</span><span className="font-display text-lg font-black">Halftone</span></div>
          <button type="button" onClick={startConversation} className="mt-2 flex items-center gap-2 rounded-xl border border-line px-3 py-2 text-sm font-semibold hover:border-ink"><MessageSquarePlus className="size-4" />New design</button>
          <p className="mt-5 px-2 font-mono text-[9px] font-bold uppercase text-muted">Your designs</p>
          <ul className="mt-1 flex-1 space-y-0.5 overflow-y-auto">
            {conversations.map((conversation) => (
              <li key={conversation.id} className={`group flex items-center rounded-lg ${conversation.id === active?.id ? "bg-paper" : "hover:bg-paper/70"}`}>
                <button type="button" onClick={() => { setActiveId(conversation.id); setSidebarOpen(false); }} className="min-w-0 flex-1 truncate px-3 py-2 text-left text-[13px]">{conversation.title}</button>
                <button type="button" aria-label="Delete conversation" onClick={() => deleteConversation(conversation.id)} className="mr-1 hidden p-1 text-muted hover:text-press group-hover:block"><Trash2 className="size-3.5" /></button>
              </li>
            ))}
          </ul>
          <div className="mt-3 space-y-1 border-t border-line pt-3 font-mono text-[10px] font-bold uppercase text-muted">
            <Link to="/setup" className="block px-2 py-1 hover:text-ink">Store setup</Link>
            <Link to="/studio" className="block px-2 py-1 hover:text-ink">Form view</Link>
          </div>
        </div>
      </aside>
      {sidebarOpen ? <button type="button" aria-label="Close menu" onClick={() => setSidebarOpen(false)} className="no-print fixed inset-0 z-10 bg-ink/30 lg:hidden" /> : null}

      {/* Conversation */}
      <main className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="no-print flex items-center gap-3 border-b border-line px-4 py-3 lg:px-6">
          <button type="button" aria-label="Open menu" onClick={() => setSidebarOpen(true)} className="rounded-lg p-1.5 text-muted hover:bg-card hover:text-ink lg:hidden"><PanelLeft className="size-5" /></button>
          <p className="min-w-0 flex-1 truncate text-sm font-semibold">{active?.title ?? "New design"}</p>
          <span className="hidden font-mono text-[10px] font-bold uppercase text-press sm:block">● Print ready</span>
        </header>

        <section className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-3 py-6 sm:px-5">
            {empty ? (
              <div className="no-print mt-10 text-center">
                <span className="mx-auto grid size-12 place-items-center rounded-full bg-poster text-poster-foreground"><Sparkles className="size-5" /></span>
                <h1 className="mt-5 font-display text-3xl font-black leading-tight sm:text-4xl">What are we making today?</h1>
                <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-muted">Describe the venue, the dishes or offers and the exact prices. I compose the design, keep every detail you typed, and you refine it by chatting.</p>
                <div className="mt-8 grid gap-2 sm:grid-cols-2">
                  {STARTERS.map((starter) => (
                    <button key={starter.title} type="button" onClick={() => setDraft(starter.text)} className="rounded-2xl border border-line bg-card p-4 text-left transition-colors hover:border-ink">
                      <p className="text-sm font-semibold">{starter.title}</p>
                      <p className="mt-1 line-clamp-2 text-xs leading-snug text-muted">{starter.text}</p>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                {active.messages.map((message) => (
                  <ChatMessageView key={message.id} message={message} shots={shots[message.id]} setup={setup} printing={printingId === message.id} onPhotos={(id) => { const menu = active.messages.find((item) => item.id === id)?.menu; if (menu) void shootPhotos(id, menu); }} onPrint={printMessage} busy={busy} />
                ))}
              </div>
            )}
            <div ref={endRef} />
          </div>
        </section>

        {active ? (
          <ChatComposer value={draft} onChange={setDraft} onSend={() => void send()} busy={busy} attachments={attachments} onAttach={(kind, file) => void attach(kind, file)} onRemove={(kind) => setAttachments((previous) => ({ ...previous, [kind]: undefined }))} settings={active.settings} onSettings={(next) => patchConversation(active.id, (conversation) => ({ ...conversation, settings: { ...conversation.settings, ...next } }))} setup={setup} onClearSetup={dropSetup} error={error} />
        ) : null}
      </main>
    </div>
  );
}
