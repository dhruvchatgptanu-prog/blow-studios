import { createFileRoute, Link } from "@tanstack/react-router";
import { useServerFn } from "@tanstack/react-start";
import { MessageSquarePlus, PanelLeft, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChatComposer, MAX_MENU_PHOTOS, type Attachments } from "@/components/ChatComposer";
import { ChatMessageView, type MessageShots } from "@/components/ChatMessageView";
import type { Shot } from "@/components/MenuSheet";
import { chooseLook, conversationTitle, describeResult, extendBrief, HELP_REPLY, nextLook, parseIntent, STARTERS, type Intent, type LookRef } from "@/lib/ai/intent";
import { signAssets, uploadAssets, type AssetRef } from "@/lib/assets.functions";
import { assetPaths, DEFAULT_SETTINGS, latestDesign, loadConversations, newConversation, saveConversations, uid, withSignedUrls, type ChatMessage, type ChatSettings, type Conversation, type RenderSettings } from "@/lib/chat";
import { downscaleImage, validateImage } from "@/lib/images";
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

function shotsFromPhotos(message: ChatMessage): MessageShots | undefined {
  if (!message.photos) return undefined;
  const dishes: Shot[] = [];
  message.photos.dishes.forEach((dish, index) => { if (dish?.url) dishes[index] = { label: dish.label ?? `Dish ${index + 1}`, src: dish.url, final: true }; });
  return { hero: message.photos.hero?.url ? { label: message.photos.hero.label ?? "Hero", src: message.photos.hero.url, final: true } : null, dishes, progress: 0, busy: false };
}

function ChatStudio() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Attachments>({ menus: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shots, setShots] = useState<Record<string, MessageShots>>({});
  const [previews, setPreviews] = useState<Record<string, string[]>>({});
  const [printingId, setPrintingId] = useState<string | null>(null);
  const [setup, setSetup] = useState<StudioSetup | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const run = useServerFn(generateMenu);
  const upload = useServerFn(uploadAssets);
  const sign = useServerFn(signAssets);

  // Load saved conversations, re-sign their stored photos, and pick up the store wizard result.
  useEffect(() => {
    const saved = loadConversations();
    const wizard = loadSetup();
    setSetup(wizard);
    const settings: ChatSettings = wizard
      ? { mode: wizard.template.mode, style: wizard.template.style, size: rendererSizeFor(wizard.display, wizard.template.mode), pages: "1", display: wizard.display }
      : DEFAULT_SETTINGS;
    const first = saved[0] ?? newConversation(settings);
    const initial = saved.length ? saved : [first];
    setConversations(initial);
    setActiveId(first.id);
    setHydrated(true);
    const paths = assetPaths(initial);
    const apply = (list: Conversation[]) => {
      const restored: Record<string, MessageShots> = {};
      for (const conversation of list) for (const message of conversation.messages) { const restoredShots = shotsFromPhotos(message); if (restoredShots) restored[message.id] = restoredShots; }
      setShots(restored);
    };
    if (paths.length === 0) { apply(initial); return; }
    sign({ data: { paths } }).then((urls) => { const signed = withSignedUrls(initial, urls); setConversations(signed); apply(signed); }).catch(() => apply(initial));
  }, [sign]);

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

  const replaceMessage = useCallback((id: string, messageId: string, patch: Partial<ChatMessage> | ((message: ChatMessage) => Partial<ChatMessage>)) => {
    patchConversation(id, (conversation) => ({ ...conversation, updatedAt: new Date().toISOString(), messages: conversation.messages.map((message) => (message.id === messageId ? { ...message, ...(typeof patch === "function" ? patch(message) : patch) } : message)) }));
  }, [patchConversation]);

  function startConversation() {
    const fresh = newConversation(active?.settings ?? DEFAULT_SETTINGS);
    setConversations((previous) => [fresh, ...previous]);
    setActiveId(fresh.id);
    setDraft(""); setAttachments({ menus: [] }); setError(null); setSidebarOpen(false);
  }

  function deleteConversation(id: string) {
    setConversations((previous) => {
      const next = previous.filter((conversation) => conversation.id !== id);
      if (next.length === 0) { const fresh = newConversation(DEFAULT_SETTINGS); setActiveId(fresh.id); return [fresh]; }
      if (id === activeId) setActiveId(next[0]!.id);
      return next;
    });
  }

  async function attach(kind: "menu" | "food", files: File[]) {
    if (files.length === 0) return;
    try {
      for (const file of files) await validateImage(file);
      setAttachments((previous) => kind === "food" ? { ...previous, food: files[0] } : { ...previous, menus: [...previous.menus, ...files].slice(0, MAX_MENU_PHOTOS) });
      setError(null);
    } catch (failure) { setError((failure as Error).message); }
  }

  function removeAttachment(kind: "menu" | "food", index?: number) {
    setAttachments((previous) => kind === "food" ? { ...previous, food: undefined } : { ...previous, menus: previous.menus.filter((_, position) => position !== index) });
  }

  function dropSetup() { clearSetup(); setSetup(null); }

  /** Stores photos in Lovable Cloud and attaches the references to a message. Never blocks the design. */
  function persistAttachments(conversationId: string, messageId: string, items: { kind: "menu" | "food"; dataUrl: string; label: string }[]) {
    if (items.length === 0) return;
    upload({ data: { items: items.map((item) => ({ ...item, conversationId })) } })
      .then((result) => {
        if (result.assets.length === 0) return;
        replaceMessage(conversationId, messageId, (message) => ({ attachments: (message.attachments ?? []).map((attachment, index) => ({ ...attachment, asset: result.assets[index] ?? attachment.asset })) }));
      })
      .catch(() => { /* photos still worked for this design; storage is best-effort */ });
  }

  function persistRender(conversationId: string, messageId: string, slot: "hero" | number, dataUrl: string, label: string) {
    upload({ data: { items: [{ kind: "render", dataUrl, label, conversationId }] } })
      .then((result) => {
        const asset: AssetRef | undefined = result.assets[0];
        if (!asset) return;
        replaceMessage(conversationId, messageId, (message) => {
          const photos = message.photos ?? { dishes: [] };
          if (slot === "hero") return { photos: { ...photos, hero: asset } };
          const dishes = [...photos.dishes]; dishes[slot] = asset;
          return { photos: { ...photos, dishes } };
        });
      })
      .catch(() => { /* best-effort */ });
  }

  async function send() {
    if (!active || busy) return;
    const text = draft.trim();
    const conversationId = active.id;
    const current = latestDesign(active);
    const foodFile = attachments.food;
    const menuFiles = attachments.menus;
    const userMessage: ChatMessage = {
      id: uid(), role: "user", at: new Date().toISOString(),
      text: text || (foodFile ? DEFAULT_FOOD_INSTRUCTION : menuFiles.length ? `Redesign my current menu from ${menuFiles.length === 1 ? "this photo" : `these ${menuFiles.length} photos`}.` : ""),
      attachments: [...menuFiles.map((file) => ({ kind: "menu" as const, name: file.name })), ...(foodFile ? [{ kind: "food" as const, name: foodFile.name }] : [])],
    };
    setPreviews((previous) => ({ ...previous, [userMessage.id]: [...menuFiles, ...(foodFile ? [foodFile] : [])].map((file) => URL.createObjectURL(file)) }));
    setDraft(""); setAttachments({ menus: [] }); setError(null);

    // A food photo improves the current design's photography; no engine call needed.
    if (foodFile && current) {
      appendMessage(conversationId, userMessage);
      const foodData = await downscaleImage(foodFile, 2048, 0.92);
      persistAttachments(conversationId, userMessage.id, [{ kind: "food", dataUrl: foodData, label: foodFile.name }]);
      await improvePhoto(conversationId, current.message.id, current.menu, foodFile, text || DEFAULT_FOOD_INSTRUCTION);
      return;
    }

    const intent = parseIntent(userMessage.text, {
      hasMenu: Boolean(current) && menuFiles.length === 0,
      mode: active.settings.mode, size: active.settings.size, pages: active.settings.pages, style: active.settings.style,
      display: active.settings.display ?? setup?.display, look: active.settings.look, setupTemplateId: setup?.template.id,
    });
    const settings: ChatSettings = { mode: intent.mode, style: intent.style, size: intent.size, pages: intent.pages, display: intent.display, look: intent.look };
    const title = active.messages.length === 0 ? conversationTitle(text || menuFiles[0]?.name || "New design") : active.title;
    appendMessage(conversationId, userMessage, { settings, title });

    if (intent.kind === "help") { appendMessage(conversationId, { id: uid(), role: "assistant", at: new Date().toISOString(), text: HELP_REPLY, status: "done" }); return; }
    if (intent.kind === "photos" && current) { appendMessage(conversationId, { id: uid(), role: "assistant", at: new Date().toISOString(), text: "Creating the photos for the design above now.", status: "done" }); await shootPhotos(conversationId, current.message.id, current.menu); return; }
    if (intent.kind === "restyle" && current && intent.look) { restyle(conversationId, current.message, intent.look); return; }

    await compose(conversationId, intent, settings, current && intent.kind === "revise" ? current.menu : null, menuFiles, userMessage.id, active.brief);
  }

  /** Re-renders the latest design in another template; instant, no engine call. */
  function restyle(conversationId: string, source: ChatMessage, look: LookRef) {
    if (!source.menu || !source.render) return;
    const render: RenderSettings = { ...source.render, look, style: source.render.mode === "poster" ? "bold-deals" : look.style === "bold-deals" ? "editorial" : look.style };
    const id = uid();
    appendMessage(conversationId, { id, role: "assistant", at: new Date().toISOString(), text: `Same design in the ${look.name} look. Say "another look" to keep cycling, or name one (${look.alternatives.slice(0, 3).map((alt) => alt.replace("-", " ")).join(", ")}…).`, menu: source.menu, render, photos: source.photos, status: "done" }, { settings: { ...(conversations.find((conversation) => conversation.id === conversationId)?.settings ?? DEFAULT_SETTINGS), look, style: render.style } });
    const sourceShots = shots[source.id];
    if (sourceShots) setShots((all) => ({ ...all, [id]: { ...sourceShots, busy: false } }));
  }

  async function compose(conversationId: string, intent: Intent, settings: ChatSettings, previous: GeneratedMenu | null, menuFiles: File[], userMessageId: string, runningBrief: string) {
    const brief = previous ? extendBrief(runningBrief || intent.brief, intent.brief) : intent.brief;
    const pendingId = uid();
    appendMessage(conversationId, { id: pendingId, role: "assistant", at: new Date().toISOString(), text: previous ? "Revising with every engine…" : menuFiles.length ? `Reading ${menuFiles.length === 1 ? "your menu photo" : `all ${menuFiles.length} photos`} and redesigning with every engine…` : "Composing with every engine…", status: "thinking" }, { brief });
    setBusy(true);
    try {
      const referenceImages = await Promise.all(menuFiles.map((file) => downscaleImage(file)));
      persistAttachments(conversationId, userMessageId, referenceImages.map((dataUrl, index) => ({ kind: "menu", dataUrl, label: menuFiles[index]?.name ?? `page ${index + 1}` })));
      const result = await run({ data: {
        brief, mode: settings.mode, style: settings.style, pages: settings.pages, size: settings.size,
        ...(previous ? { previous: JSON.stringify(stripEngine(previous)) } : {}),
        ...(referenceImages.length ? { referenceImages } : {}),
        ...(intent.display ? { display: intent.display } : {}),
        ...(intent.look ? { template: intent.look.id } : {}),
        ...(setup ? { store: storeContext(setup.profile), ...(setup.storeId ? { storeId: setup.storeId } : {}), ...(setup.displayId ? { displayId: setup.displayId } : {}) } : {}),
      } });
      const render: RenderSettings = { mode: settings.mode, style: settings.style, size: settings.size, pages: settings.pages, display: intent.display, look: intent.look };
      const wantsPhotos = intent.wantsPhotos || (intent.kind === "new" && settings.autoPhotos !== false);
      replaceMessage(conversationId, pendingId, { text: describeResult(result, { ...intent, wantsPhotos }), menu: result, render, status: "done" });
      if (wantsPhotos) { setBusy(false); await shootPhotos(conversationId, pendingId, result); }
    } catch (failure) {
      replaceMessage(conversationId, pendingId, { text: `That did not work: ${(failure as Error).message}`, status: "error" });
    } finally {
      setBusy(false);
    }
  }

  function setShot(messageId: string, patch: (previous: MessageShots) => MessageShots) {
    setShots((all) => ({ ...all, [messageId]: patch(all[messageId] ?? { hero: null, dishes: [], progress: 0, busy: false }) }));
  }

  async function shootPhotos(conversationId: string, messageId: string, menu: GeneratedMenu) {
    setShot(messageId, () => ({ hero: null, dishes: [], progress: 0, busy: true }));
    replaceMessage(conversationId, messageId, { photos: { dishes: [] } });
    try {
      if (menu.mode === "menu" && menu.heroPhoto) {
        const label = menu.heroPhoto.label || menu.restaurantName;
        await streamImage(`${menu.heroPhoto.prompt} Wide banner crop.`, (src, final) => { setShot(messageId, (previous) => ({ ...previous, hero: { label, src, final } })); if (final) persistRender(conversationId, messageId, "hero", src, label); });
      }
      const items = menu.sections.flatMap((section) => section.items);
      const list: { label: string; prompt: string }[] = (menu.mode === "poster" ? items.map((item) => ({ label: item.name, prompt: item.photoPrompt })) : menu.dishPhotos).slice(0, menu.mode === "poster" ? 4 : 3);
      for (let index = 0; index < list.length; index++) {
        const shot = list[index]!;
        setShot(messageId, (previous) => ({ ...previous, progress: index + 1 }));
        await streamImage(`${shot.prompt} Square composition with the complete advertised meal fully visible and clean edges.`, (src, final) => { setShot(messageId, (previous) => { const dishes = [...previous.dishes]; dishes[index] = { label: shot.label, src, final }; return { ...previous, dishes }; }); if (final) persistRender(conversationId, messageId, index, src, shot.label); });
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
      await streamImage(form, (src, final) => { setShot(messageId, (previous) => { const dishes = [...previous.dishes]; dishes[index] = { label: target?.label ?? file.name, src, final }; return { ...previous, dishes }; }); if (final) persistRender(conversationId, messageId, index, src, target?.label ?? file.name); });
      setShot(messageId, (previous) => ({ ...previous, busy: false }));
      replaceMessage(conversationId, replyId, { text: `Placed the improved photo as "${target?.label ?? "the first dish"}" in the design above and saved it. Say which other dish to shoot, or "create the photos" for all of them.`, status: "done" });
    } catch (failure) {
      setShot(messageId, (previous) => ({ ...previous, busy: false }));
      replaceMessage(conversationId, replyId, { text: `The photo could not be improved: ${(failure as Error).message}`, status: "error" });
    }
  }

  function printMessage(messageId: string) {
    setPrintingId(messageId);
    window.setTimeout(() => { window.print(); window.setTimeout(() => setPrintingId(null), 500); }, 80);
  }

  function restyleFromButton(messageId: string) {
    if (!active) return;
    const source = active.messages.find((message) => message.id === messageId);
    if (!source?.menu || !source.render) return;
    const look = source.render.look ? nextLook(source.render.look) : chooseLook(active.brief, source.render.mode, { setupTemplateId: setup?.template.id }, true);
    restyle(active.id, source, look);
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
                <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-muted">Describe the venue, the dishes, the exact prices and the real size of the board or page. Drop in photos of your current menu to redesign it. Every type of venue gets its own look, and you refine it by chatting.</p>
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
                  <ChatMessageView key={message.id} message={message} shots={shots[message.id]} previews={previews[message.id]} setup={setup} printing={printingId === message.id} onPhotos={(id) => { const menu = active.messages.find((item) => item.id === id)?.menu; if (menu) void shootPhotos(active.id, id, menu); }} onPrint={printMessage} onRestyle={restyleFromButton} busy={busy} />
                ))}
              </div>
            )}
            <div ref={endRef} />
          </div>
        </section>

        {active ? (
          <ChatComposer value={draft} onChange={setDraft} onSend={() => void send()} busy={busy} attachments={attachments} onAttach={(kind, files) => void attach(kind, files)} onRemove={removeAttachment} settings={active.settings} onSettings={(next) => patchConversation(active.id, (conversation) => ({ ...conversation, settings: { ...conversation.settings, ...next } }))} setup={setup} onClearSetup={dropSetup} error={error} />
        ) : null}
      </main>
    </div>
  );
}
