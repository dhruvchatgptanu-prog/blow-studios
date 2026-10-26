import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { analyzeStoreOnServer, measureDisplayOnServer } from "@/lib/ai/store.server";
import type { DisplaySpec } from "@/lib/ai/display";
import type { TemplateMatch } from "@/lib/ai/templates";
import type { StoreProfile } from "@/lib/ai/store";

export type { DisplaySpec } from "@/lib/ai/display";
export type { StoreProfile } from "@/lib/ai/store";
export type { Template, TemplateMatch } from "@/lib/ai/templates";

const imageSchema = z.string().max(12_000_000).regex(/^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/, "Upload a JPG, PNG or WebP image.");

const analyzeInput = z.object({
  description: z.string().min(10).max(3000),
  venueName: z.string().max(120).optional(),
  images: z.array(imageSchema).max(4).optional(),
});

const measureInput = z.object({
  image: imageSchema,
  imageAspect: z.number().min(0.2).max(5).optional(),
  widthCm: z.number().min(5).max(3000).optional(),
  heightCm: z.number().min(5).max(3000).optional(),
  viewingDistanceM: z.number().min(0.3).max(50).optional(),
  storeId: z.string().uuid().optional(),
  hint: z.string().max(500).optional(),
});

export type AnalyzeStoreResult = { storeId?: string | undefined; profile: StoreProfile; templates: TemplateMatch[]; engine: { provider: string; model: string } | null; warnings: string[] };
export type MeasureDisplayResult = { displayId?: string | undefined; spec: DisplaySpec; engine: { provider: string; model: string } | null; warnings: string[] };

/** Step 1 of the wizard: description + photos → store profile + recommended templates. */
export const analyzeStore = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => analyzeInput.parse(data))
  .handler(async ({ data }): Promise<AnalyzeStoreResult> => analyzeStoreOnServer(data));

/** Step 2 of the wizard: display photo (+ optional known size) → display spec with a legibility budget. */
export const measureDisplay = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => measureInput.parse(data))
  .handler(async ({ data }): Promise<MeasureDisplayResult> => measureDisplayOnServer(data));
