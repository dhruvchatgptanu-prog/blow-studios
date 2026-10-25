import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { composeMenu } from "@/lib/ai/ensemble.server";
import type { GeneratedMenu } from "@/lib/ai/schema";

export type { EngineCandidate, EngineReport, GeneratedMenu, MenuItem, MenuSection, PhotoBrief } from "@/lib/ai/schema";
export { stripEngine } from "@/lib/ai/schema";

const referenceImageSchema = z.string().max(12_000_000).refine((value) => {
  const match = /^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$/.exec(value);
  if (!match) return false;
  const mime = match[1];
  const head = match[2]?.slice(0, 16) ?? "";
  if (mime === "image/png") return head.startsWith("iVBORw0KGgo");
  if (mime === "image/jpeg") return head.startsWith("/9j/");
  return mime === "image/webp" && head.startsWith("UklGR");
}, "Upload a valid JPG, PNG or WebP menu image.");

const inputSchema = z.object({
  brief: z.string().min(3).max(4000),
  style: z.enum(["editorial", "modern", "rustic", "bold-deals"]),
  mode: z.enum(["menu", "poster"]),
  pages: z.enum(["1", "2"]),
  size: z.enum(["letter", "a5", "poster", "wide", "fascia-447", "fascia-180", "square"]),
  previous: z.string().max(60_000).optional(),
  referenceImage: referenceImageSchema.optional(),
});

/**
 * Generate a menu with every configured engine at once (Halftone built-in via
 * the Lovable gateway, ChatGPT via OpenAI, Claude via Anthropic), verify each
 * candidate deterministically, then let the judge merge the strongest parts.
 * See `src/lib/ai/ensemble.server.ts`.
 */
export const generateMenu = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => inputSchema.parse(data))
  .handler(async ({ data }): Promise<GeneratedMenu> => composeMenu(data));
