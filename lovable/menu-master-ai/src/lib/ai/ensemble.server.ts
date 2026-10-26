import { detectCuisine, enrichMenuPhotos } from "./cuisine";
import { loadMemory, recordGeneration } from "./memory.server";
import { availableProviders, pickJudge, type Provider } from "./providers.server";
import type { EngineCandidate, EngineReport, GeneratedMenu, GeneratedMenuCore, MenuRequest } from "./schema";
import { verifyMenu } from "./verify";

/**
 * Halftone ensemble.
 *
 *   brief ─► cuisine detection + learning memory (exemplars, judge ranking)
 *         ─┬─► Halftone built-in (Lovable gateway) ─┐
 *          ├─► ChatGPT (OpenAI)                    ├─► verifier ─► judge (merge + fix) ─► verifier
 *          └─► Claude (Anthropic)                  ┘                                        │
 *                                                        repair pass (only if issues remain) ◄┘
 *                                                        ─► photo-prompt enrichment ─► record to memory ─► result
 *
 * All engines run in parallel. Any engine can fail or be unconfigured; the
 * app always returns the best menu it could produce. A judged merge or a
 * repair is only kept when it verifies at least as well as what it replaces.
 */

const GENERATE_TIMEOUT_MS = Number(process.env["HALFTONE_GENERATE_TIMEOUT_MS"] ?? 110_000);
const JUDGE_TIMEOUT_MS = Number(process.env["HALFTONE_JUDGE_TIMEOUT_MS"] ?? 90_000);
const REPAIR_TIMEOUT_MS = Number(process.env["HALFTONE_REPAIR_TIMEOUT_MS"] ?? 60_000);
const PERFECT = 100;

type Attempt = { provider: Provider; candidate: EngineCandidate; menu?: GeneratedMenuCore };

function timed<T>(ms: number, run: (signal: AbortSignal) => Promise<T>): Promise<{ value?: T; error?: string; ms: number }> {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return run(controller.signal)
    .then((value) => ({ value, ms: Date.now() - started }))
    .catch((error: unknown) => ({ error: controller.signal.aborted ? "Timed out." : error instanceof Error ? error.message : "Failed.", ms: Date.now() - started }))
    .finally(() => clearTimeout(timer));
}

async function runProvider(provider: Provider, request: MenuRequest): Promise<Attempt> {
  const result = await timed(GENERATE_TIMEOUT_MS, (signal) => provider.generate(request, signal));
  if (result.value) {
    const verification = verifyMenu(result.value.value, request);
    return {
      provider,
      menu: result.value.value,
      candidate: { provider: provider.id, model: result.value.model, status: "ok", score: verification.score, issues: verification.issues, ms: result.ms },
    };
  }
  return { provider, candidate: { provider: provider.id, model: provider.model, status: "failed", score: 0, issues: [], error: result.error ?? "Engine failed.", ms: result.ms } };
}

export async function composeMenu(input: MenuRequest): Promise<GeneratedMenu> {
  const startedAt = Date.now();
  const providers = availableProviders();
  if (providers.length === 0) throw new Error("AI is not configured yet. Add LOVABLE_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY.");

  // 1. Deterministic context: cuisine + what the memory has learned.
  const cuisine = detectCuisine(input.brief);
  const memory = await loadMemory(input, cuisine);
  const request: MenuRequest = { ...input, cuisine, exemplars: memory.exemplars || undefined };

  // 2. Every engine at once.
  const attempts = await Promise.all(providers.map((provider) => runProvider(provider, request)));
  const successes = attempts.filter((attempt): attempt is Attempt & { menu: GeneratedMenuCore } => Boolean(attempt.menu));
  const candidates = attempts.map((attempt) => attempt.candidate);
  if (successes.length === 0) {
    const reasons = attempts.map((attempt) => `${attempt.provider.label}: ${attempt.candidate.error ?? "failed"}`).join(" · ");
    throw new Error(`Every engine failed. ${reasons}`);
  }
  successes.sort((a, b) => b.candidate.score - a.candidate.score || a.candidate.ms - b.candidate.ms);
  const best = successes[0]!;

  let menu: GeneratedMenuCore = best.menu;
  let score = best.candidate.score;
  let issues = best.candidate.issues;
  const report: EngineReport = {
    strategy: successes.length === 1 ? "single" : "best-candidate",
    candidates,
    cuisine,
    memory: { exemplars: memory.exemplarCount, totalRecorded: memory.totalRecorded, judgeRanking: memory.judgeRanking },
    finalScore: score,
    finalIssues: issues,
    totalMs: 0,
  };

  // 3. Judge merges when there is more than one candidate.
  const judge = pickJudge([], memory.judgeRanking);
  if (successes.length > 1 && judge) {
    const result = await timed(JUDGE_TIMEOUT_MS, (signal) =>
      judge.judge(
        { request, candidates: successes.map((attempt) => ({ provider: attempt.provider.id, model: attempt.provider.model, menu: attempt.menu, score: attempt.candidate.score, issues: attempt.candidate.issues })) },
        signal,
      ),
    );
    if (result.value) {
      const judged = result.value.value;
      const verification = verifyMenu(judged.menu, request);
      const keep = verification.score >= score || (score < PERFECT && verification.score >= score - 5);
      report.judge = {
        provider: judge.id,
        model: result.value.model,
        winner: judged.winner,
        rationale: keep ? judged.rationale : `${judged.rationale} (Merge scored ${verification.score}, below the best candidate at ${score}, so the best candidate was kept.)`,
        mergedFrom: judged.mergedFrom,
        ms: result.ms,
      };
      if (keep) {
        report.strategy = "judged-merge";
        menu = judged.menu;
        score = verification.score;
        issues = verification.issues;
      }
    } else {
      report.judge = { provider: judge.id, model: judge.model, winner: best.provider.id, rationale: `Judge unavailable (${result.error}); best verified candidate used.`, mergedFrom: [best.provider.id], ms: result.ms };
    }
  }

  // 4. One targeted repair pass if the verifier still lists issues.
  const repairer = judge ?? providers[0];
  if (issues.length > 0 && score < PERFECT && repairer) {
    const result = await timed(REPAIR_TIMEOUT_MS, (signal) => repairer.repair({ request, menu, issues }, signal));
    if (result.value) {
      const verification = verifyMenu(result.value.value, request);
      const applied = verification.score > score;
      report.repair = { provider: repairer.id, model: result.value.model, before: score, after: verification.score, applied, ms: result.ms };
      if (applied) {
        menu = result.value.value;
        score = verification.score;
        issues = verification.issues;
      }
    } else {
      report.repair = { provider: repairer.id, model: repairer.model, before: score, after: score, applied: false, ms: result.ms };
    }
  }

  // 5. Deterministic photo-prompt enrichment (style pipeline untouched), then final verification.
  menu = enrichMenuPhotos(menu, cuisine);
  const finalVerification = verifyMenu(menu, request);
  report.finalScore = finalVerification.score;
  report.finalIssues = finalVerification.issues;
  report.totalMs = Date.now() - startedAt;

  // 6. Learn from it (never blocks the response on failure).
  await recordGeneration(input, cuisine, menu, report, report.totalMs);

  return { ...menu, engine: report };
}
