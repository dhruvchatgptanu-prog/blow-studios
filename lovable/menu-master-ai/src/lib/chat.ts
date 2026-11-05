import type { ChatMode, ChatPages, ChatSize, ChatStyle } from "@/lib/ai/intent";
import type { GeneratedMenu } from "@/lib/menu.functions";

/**
 * Conversation state for the chat studio. Conversations live in localStorage
 * so the owner can come back to a design; photos are kept in memory only
 * (they are large data URLs) and are recreated on demand.
 */

export type ChatAttachment = { kind: "menu" | "food"; name: string };

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  at: string;
  attachments?: ChatAttachment[] | undefined;
  /** The design this assistant message delivered. */
  menu?: GeneratedMenu | undefined;
  /** Render settings for that design. */
  render?: { mode: ChatMode; style: ChatStyle; size: ChatSize; pages: ChatPages } | undefined;
  status?: "thinking" | "done" | "error" | undefined;
};

export type ChatSettings = { mode: ChatMode; style: ChatStyle; size: ChatSize; pages: ChatPages };

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

const KEY = "halftone.chats.v1";
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
