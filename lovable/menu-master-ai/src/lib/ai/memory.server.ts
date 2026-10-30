import type { SupabaseClient } from "@supabase/supabase-js";

import type { EngineReport, GeneratedMenuCore, MenuRequest, ProviderId } from "./schema";

/**
 * Halftone learning memory (Supabase table `halftone_generations`).
 *
 * Every generation is recorded with its engine scores. Two things are read
 * back on later runs:
 *   1. Exemplars — past menus that scored 100/100 for the same mode and
 *      cuisine, shown to the engines as "approved output" (few-shot).
 *   2. Judge ranking — which judge model produced the best verified results,
 *      so the ensemble promotes it automatically.
 *
 * Every call is guarded: memory can never break or slow a generation beyond
 * a short timeout.
 */

const TIMEOUT_MS = Number(process.env["HALFTONE_MEMORY_TIMEOUT_MS"] ?? 2500);
const MIN_SAMPLES = 5;

type Row = {
  id?: string;
  created_at?: string;
  brief: string;
  mode: string;
  style: string;
  size: string;
  pages: string;
  cuisine: string | null;
  is_revision: boolean;
  had_reference_image: boolean;
  strategy: string;
  judge_provider: string | null;
  winner: string | null;
  final_score: number;
  final_issues: string[];
  candidates: unknown;
  menu: GeneratedMenuCore;
  duration_ms: number | null;
  store_id?: string | null;
  display_id?: string | null;
  template_id?: string | null;
};

async function db(): Promise<SupabaseClient | undefined> {
  if (process.env["HALFTONE_MEMORY"] === "off") return undefined;
  if (!process.env["SUPABASE_URL"] || !process.env["SUPABASE_SERVICE_ROLE_KEY"]) return undefined;
  try {
    const mod = await import("@/integrations/supabase/client.server");
    return mod.supabaseAdmin as unknown as SupabaseClient;
  } catch {
    return undefined;
  }
}

/** Resolves to undefined on timeout or error so memory can never break a generation. */
function withTimeout<T>(promise: PromiseLike<T>): Promise<T | undefined> {
  return new Promise<T | undefined>((resolve) => {
    const timer = setTimeout(() => resolve(undefined), TIMEOUT_MS);
    Promise.resolve(promise).then(
      (value) => { clearTimeout(timer); resolve(value); },
      () => { clearTimeout(timer); resolve(undefined); },
    );
  });
}

export type MemoryContext = {
  exemplars: string;
  exemplarCount: number;
  judgeRanking: ProviderId[];
  totalRecorded: number;
};

const EMPTY: MemoryContext = { exemplars: "", exemplarCount: 0, judgeRanking: [], totalRecorded: 0 };

/** Loads exemplars and the judge ranking for this request. Never throws. */
export async function loadMemory(request: MenuRequest, cuisines: string[]): Promise<MemoryContext> {
  const client = await db();
  if (!client) return EMPTY;
  const cuisine = cuisines[0] ?? null;

  const exemplarQuery = client
    .from("halftone_generations")
    .select("brief, menu, final_score, created_at")
    .eq("mode", request.mode)
    .eq("final_score", 100)
    .order("created_at", { ascending: false })
    .limit(2);
  const exemplarsPromise = withTimeout(cuisine ? exemplarQuery.eq("cuisine", cuisine) : exemplarQuery.is("cuisine", null));
  const statsPromise = withTimeout(
    client.from("halftone_generations").select("judge_provider, final_score, strategy").not("judge_provider", "is", null).order("created_at", { ascending: false }).limit(60),
  );
  const countPromise = withTimeout(client.from("halftone_generations").select("id", { count: "exact", head: true }));

  const [exemplars, stats, total] = await Promise.all([exemplarsPromise, statsPromise, countPromise]);

  const rows = ((exemplars?.data ?? []) as Pick<Row, "brief" | "menu" | "final_score" | "created_at">[]).filter((row) => row.brief.trim() !== request.brief.trim());
  const exemplarText = rows.length
    ? rows
        .map((row, index) => `### APPROVED EXAMPLE ${index + 1} (scored 100/100 on its own brief)\nBRIEF: ${row.brief.slice(0, 600)}\nOUTPUT: ${JSON.stringify(stripForExemplar(row.menu)).slice(0, 3500)}`)
        .join("\n\n")
    : "";

  const byJudge = new Map<string, { sum: number; n: number }>();
  for (const row of (stats?.data ?? []) as { judge_provider: string | null; final_score: number; strategy: string }[]) {
    if (!row.judge_provider || row.strategy !== "judged-merge") continue;
    const entry = byJudge.get(row.judge_provider) ?? { sum: 0, n: 0 };
    entry.sum += row.final_score;
    entry.n += 1;
    byJudge.set(row.judge_provider, entry);
  }
  const ranking = Array.from(byJudge.entries())
    .filter(([, value]) => value.n >= MIN_SAMPLES)
    .sort((a, b) => b[1].sum / b[1].n - a[1].sum / a[1].n)
    .map(([key]) => key as ProviderId);

  return { exemplars: exemplarText, exemplarCount: rows.length, judgeRanking: ranking, totalRecorded: total?.count ?? 0 };
}

function stripForExemplar(menu: GeneratedMenuCore) {
  // Keep structure and copy; drop long photo prompts to save tokens.
  return {
    ...menu,
    heroPhoto: { label: menu.heroPhoto.label, prompt: "…" },
    dishPhotos: menu.dishPhotos.map((photo) => ({ label: photo.label, prompt: "…" })),
    sections: menu.sections.map((section) => ({ ...section, items: section.items.map((item) => ({ ...item, photoPrompt: "…" })) })),
  };
}

/** Records a finished generation. Fire-and-forget; never throws. */
export async function recordGeneration(request: MenuRequest, cuisines: string[], menu: GeneratedMenuCore, engine: EngineReport, durationMs: number): Promise<void> {
  const client = await db();
  if (!client) return;
  const row: Row = {
    brief: request.brief,
    mode: request.mode,
    style: request.style,
    size: request.size,
    pages: request.pages,
    cuisine: cuisines[0] ?? null,
    is_revision: Boolean(request.previous),
    had_reference_image: Boolean(request.referenceImage),
    strategy: engine.strategy,
    judge_provider: engine.judge?.provider ?? null,
    winner: engine.judge?.winner ?? engine.candidates.find((candidate) => candidate.status === "ok")?.provider ?? null,
    final_score: engine.finalScore,
    final_issues: engine.finalIssues,
    candidates: engine.candidates,
    menu,
    duration_ms: durationMs,
    store_id: request.storeId ?? null,
    display_id: request.displayId ?? null,
    template_id: request.template ?? null,
  };
  await withTimeout(client.from("halftone_generations").insert(row));
}
