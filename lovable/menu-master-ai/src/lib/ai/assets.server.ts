import type { SupabaseClient } from "@supabase/supabase-js";

/**
 * Halftone asset storage: the owner's menu photos, food photos and the finished
 * renders live in the private Supabase bucket `halftone-uploads`, indexed in
 * `public.halftone_assets`. The browser only ever receives short-lived signed
 * URLs; the service-role client runs here on the server. Every call is guarded:
 * storage can never break a generation or a chat turn.
 */

export const ASSET_BUCKET = "halftone-uploads";
const SIGNED_URL_SECONDS = 7 * 24 * 60 * 60;
export const ASSET_KINDS = ["menu", "food", "render", "store", "display"] as const;
export type AssetKind = (typeof ASSET_KINDS)[number];

export type AssetRef = { id: string; kind: AssetKind; path: string; url: string; label?: string | undefined; contentType: string };

async function db(): Promise<SupabaseClient | undefined> {
  if (!process.env["SUPABASE_URL"] || !process.env["SUPABASE_SERVICE_ROLE_KEY"]) return undefined;
  try {
    const mod = await import("@/integrations/supabase/client.server");
    return mod.supabaseAdmin as unknown as SupabaseClient;
  } catch {
    return undefined;
  }
}

function parseDataUrl(dataUrl: string) {
  const match = /^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$/.exec(dataUrl);
  if (!match) throw new Error("Unsupported image.");
  return { contentType: match[1] ?? "image/jpeg", bytes: Buffer.from(match[2] ?? "", "base64") };
}

function extensionFor(contentType: string) {
  return contentType === "image/png" ? "png" : contentType === "image/webp" ? "webp" : "jpg";
}

export type UploadInput = { kind: AssetKind; dataUrl: string; label?: string | undefined; conversationId?: string | undefined };

/** Stores images and returns references with signed URLs. Items that fail are reported, never thrown. */
export async function storeAssets(items: UploadInput[]): Promise<{ assets: AssetRef[]; warnings: string[] }> {
  const client = await db();
  if (!client) return { assets: [], warnings: ["Storage is not configured on the server; images were used for this design but not saved."] };
  const assets: AssetRef[] = [];
  const warnings: string[] = [];
  const month = new Date().toISOString().slice(0, 7);
  for (const item of items.slice(0, 12)) {
    try {
      const { contentType, bytes } = parseDataUrl(item.dataUrl);
      const id = globalThis.crypto.randomUUID();
      const path = `${item.kind}/${month}/${id}.${extensionFor(contentType)}`;
      const upload = await client.storage.from(ASSET_BUCKET).upload(path, bytes, { contentType, upsert: false });
      if (upload.error) { warnings.push(`${item.label ?? item.kind}: ${upload.error.message}`); continue; }
      const row = await client.from("halftone_assets").insert({ id, kind: item.kind, path, content_type: contentType, bytes: bytes.length, conversation_id: item.conversationId ?? null, label: item.label ?? null }).select("id").single();
      if (row.error) warnings.push(`${item.label ?? item.kind}: saved to storage but not indexed (${row.error.message}).`);
      const signed = await client.storage.from(ASSET_BUCKET).createSignedUrl(path, SIGNED_URL_SECONDS);
      assets.push({ id, kind: item.kind, path, url: signed.data?.signedUrl ?? "", label: item.label, contentType });
    } catch (error) {
      warnings.push(`${item.label ?? item.kind}: ${error instanceof Error ? error.message : "could not be stored"}`);
    }
  }
  return { assets, warnings };
}

/** Fresh signed URLs for stored paths (the chat calls this when it reopens a conversation). */
export async function signAssetPaths(paths: string[]): Promise<Record<string, string>> {
  const client = await db();
  const unique = Array.from(new Set(paths.filter((path) => /^(?:menu|food|render|store|display)\/[\w-]+\/[\w-]+\.(?:jpg|png|webp)$/.test(path)))).slice(0, 200);
  if (!client || unique.length === 0) return {};
  const result = await client.storage.from(ASSET_BUCKET).createSignedUrls(unique, SIGNED_URL_SECONDS);
  const map: Record<string, string> = {};
  for (const entry of result.data ?? []) if (entry.path && entry.signedUrl) map[entry.path] = entry.signedUrl;
  return map;
}

/** Downloads a stored asset as a data URL so a saved menu photo can feed a new generation. */
export async function readAssetAsDataUrl(path: string): Promise<string | undefined> {
  const client = await db();
  if (!client) return undefined;
  const file = await client.storage.from(ASSET_BUCKET).download(path);
  if (file.error || !file.data) return undefined;
  const buffer = Buffer.from(await file.data.arrayBuffer());
  const contentType = file.data.type || (path.endsWith(".png") ? "image/png" : path.endsWith(".webp") ? "image/webp" : "image/jpeg");
  return `data:${contentType};base64,${buffer.toString("base64")}`;
}
