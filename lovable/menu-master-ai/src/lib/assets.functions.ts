import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { signAssetPaths, storeAssets, type AssetRef } from "@/lib/ai/assets.server";

export type { AssetRef } from "@/lib/ai/assets.server";

const dataUrl = z.string().max(16_000_000).regex(/^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/);

const uploadInput = z.object({
  items: z.array(z.object({
    kind: z.enum(["menu", "food", "render", "store", "display"]),
    dataUrl,
    label: z.string().max(120).optional(),
    conversationId: z.string().max(64).optional(),
  })).min(1).max(12),
});

/** Saves the owner's photos and finished renders to Lovable Cloud storage; returns signed URLs. */
export const uploadAssets = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => uploadInput.parse(data))
  .handler(async ({ data }): Promise<{ assets: AssetRef[]; warnings: string[] }> => storeAssets(data.items));

const signInput = z.object({ paths: z.array(z.string().max(200)).max(200) });

/** Fresh signed URLs for stored assets (signed URLs expire after a week). */
export const signAssets = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => signInput.parse(data))
  .handler(async ({ data }): Promise<Record<string, string>> => signAssetPaths(data.paths));
