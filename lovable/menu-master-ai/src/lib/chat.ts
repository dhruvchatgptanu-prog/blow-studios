import type { DisplaySpec } from "@/lib/ai/display";
import type { ChatMode, ChatPages, ChatSize, ChatStyle, LookRef } from "@/lib/ai/intent";
import type { AssetRef } from "@/lib/assets.functions";
import type { GeneratedMenu } from "@/lib/menu.functions";

/**
 * Conversation state for the chat studio. Conversations live in localStorage;
 * photos live in Lovable Cloud storage and are referenced by path, so a design
 * and its photography survive a reload (URLs are re-signed on load).
 */

export type ChatAttachment = { kind: "menu" | "food"; name: string; asset?: AssetRef | undefined };

export type RenderSettings = { mode: ChatMode; style: ChatStyle; size: ChatSize; pages: ChatPages; display?: DisplaySpec | undefined; look?: LookRef | undefined };

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  at: string;
  attachments?: ChatAttachment[] | undefined;
  /** The design this assistant message delivered. */
  menu?: GeneratedMenu | undefined;
  /** Render settings for that design. */
  render?: RenderSettings | undefined;
  /** Stored photography for that design (hero first, then dishes in order). */
  photos?: { hero?: AssetRef | undefined; dishes: (AssetRef | null)[] } | undefined;
  status?: "thinking" | "done" | "error" | undefined;
};

export type ChatSettings = { mode: ChatMode; style: ChatStyle; size: ChatSize; pages: ChatPages; display?: DisplaySpec | undefined; look?: LookRef | undefined };

export type Conversation = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  /** Running brief: the first request plus every revision, so exact names and prices keep being verified. */
  brief: string;
  settings: ChatSettings;
  messages: ChatMessage[];
};

const KEY = "halftone.chats.v2";
const MAX_CONVERSATIONS = 30;

export const DEFAULT_SETTINGS: ChatSettings = { mode: "poster", style: "bold-deals", size: "poster", pages: "1" };

export function uid() {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

export function newConversation(settings: ChatSettings = DEFAULT_SETTINGS): Conversation {
  const now = new Date().toISOString();
  return { id: uid(), title: "New design", createdAt: now, updatedAt: now, brief: "", settings, messages: [] };
}

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Conversation[];
    return Array.isArray(parsed) ? parsed.filter((conversation) => conversation && Array.isArray(conversation.messages)) : [];
  } catch {
    return [];
  }
}

export function saveConversations(conversations: Conversation[]) {
  try {
    const kept = [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).slice(0, MAX_CONVERSATIONS);
    localStorage.setItem(KEY, JSON.stringify(kept));
  } catch {
    /* storage may be full or unavailable; the session still works in memory */
  }
}

/** The most recent design in a conversation, with its message id. */
export function latestDesign(conversation: Conversation): { message: ChatMessage; menu: GeneratedMenu } | null {
  for (let index = conversation.messages.length - 1; index >= 0; index--) {
    const message = conversation.messages[index];
    if (message?.role === "assistant" && message.menu) return { message, menu: message.menu };
  }
  return null;
}

/** Every stored asset path referenced by the conversations (to re-sign URLs on load). */
export function assetPaths(conversations: Conversation[]): string[] {
  const paths = new Set<string>();
  for (const conversation of conversations) for (const message of conversation.messages) {
    for (const attachment of message.attachments ?? []) if (attachment.asset) paths.add(attachment.asset.path);
    if (message.photos?.hero) paths.add(message.photos.hero.path);
    for (const dish of message.photos?.dishes ?? []) if (dish) paths.add(dish.path);
  }
  return Array.from(paths);
}

/** Applies fresh signed URLs to every asset reference. */
export function withSignedUrls(conversations: Conversation[], urls: Record<string, string>): Conversation[] {
  const sign = (asset: AssetRef): AssetRef => (urls[asset.path] ? { ...asset, url: urls[asset.path]! } : asset);
  return conversations.map((conversation) => ({
    ...conversation,
    messages: conversation.messages.map((message) => ({
      ...message,
      attachments: message.attachments?.map((attachment) => (attachment.asset ? { ...attachment, asset: sign(attachment.asset) } : attachment)),
      photos: message.photos ? { hero: message.photos.hero ? sign(message.photos.hero) : undefined, dishes: message.photos.dishes.map((dish) => (dish ? sign(dish) : null)) } : undefined,
    })),
  }));
}
