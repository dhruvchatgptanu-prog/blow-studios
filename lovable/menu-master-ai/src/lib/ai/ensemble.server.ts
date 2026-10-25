import { availableProviders, pickJudge, type Provider } from "./providers.server";
import type { EngineCandidate, EngineReport, GeneratedMenu, GeneratedMenuCore, MenuRequest } from "./schema";
import { verifyMenu } from "./verify";

/**
 * Halftone ensemble.
 *
 *   brief ─┬─► Halftone built-in (Lovable gateway) ─┐
 *          ├─► ChatGPT (OpenAI)                    ├─► deterministic verifier ─► judge (merge + fix) ─► verifier ─► best wins
 *          └─► Claude (Anthropic)                  ┘
 *
 * All engines run in parallel. Any engine can fail or be unconfigured; the
 * app always returns the best menu it could produce. The judged merge is
 * only kept if it verifies at least as well as the best raw candidate.
 */

const GENERATE_TIMEOUT_MS = Number(process.env["HALFTONE_GENERATE_TIMEOUT_MS"] ?? 110_000);
const JUDGE_TIMEOUT_MS = Number(process.env["HALFTONE_JUDGE_TIMEOUT_MS"] ?? 90_000);
/** A candidate at or above this score is "clean"; we still judge when ≥2 candidates exist so the merge can combine strengths. */
const PERFECT = 100;

type Attempt = { provider: Provider; candidate: EngineCandidate; menu?: GeneratedMenuCore };

async function runProvider(provider: Provider, request: MenuRequest): Promise<Attempt> {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), GENERATE_TIMEOUT_MS);
  try {
    const menu = await provider.generate(request, controller.signal);
    const verification = verifyMenu(menu, request);
    return {
      provider,
      menu,
      candidate: { provider: provider.id, model: provider.model, status: "ok", score: verification.score, issues: verification.issues, ms: Date.now() - started },
    };
  } catch (error) {
    const message = controller.signal.aborted ? "Timed out." : error instanceof Error ? error.message : "Engine failed.";
    return {
      provider,
      candidate: { provider: provider.id, model: provider.model, status: "failed", score: 0, issues: [], error: message, ms: Date.now() - started },
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function composeMenu(request: MenuRequest): Promise<GeneratedMenu> {
  const providers = availableProviders();
  if (providers.length === 0) throw new Error("AI is not configured yet. Add LOVABLE_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY.");

  const attempts = await Promise.all(providers.map((provider) => runProvider(provider, request)));
  const successes = attempts.filter((attempt): attempt is Attempt & { menu: GeneratedMenuCore } => Boolean(attempt.menu));
  const candidates = attempts.map((attempt) => attempt.candidate);

  if (successes.length === 0) {
    const reasons = attempts.map((attempt) => `${attempt.provider.label}: ${attempt.candidate.error ?? "failed"}`).join(" · ");
    throw new Error(`Every engine failed. ${reasons}`);
  }

  successes.sort((a, b) => b.candidate.score - a.candidate.score || a.candidate.ms - b.candidate.ms);
  const best = successes[0]!;

  const report: EngineReport = {
    strategy: successes.length === 1 ? "single" : "best-candidate",
    candidates,
    finalScore: best.candidate.score,
    finalIssues: best.candidate.issues,
  };

  if (successes.length === 1) {
    return { ...best.menu, engine: report };
  }

  // Judge: merge the candidates into one final menu, fixing the verifier's findings.
  const judge = pickJudge();
  if (judge) {
    const started = Date.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), JUDGE_TIMEOUT_MS);
    try {
      const judged = await judge.judge(
        {
          request,
          candidates: successes.map((attempt) => ({
            provider: attempt.provider.id,
            model: attempt.provider.model,
            menu: attempt.menu,
            score: attempt.candidate.score,
            issues: attempt.candidate.issues,
          })),
        },
        controller.signal,
      );
      const verification = verifyMenu(judged.menu, request);
      const judgeInfo = { provider: judge.id, model: judge.model, winner: judged.winner, rationale: judged.rationale, mergedFrom: judged.mergedFrom, ms: Date.now() - started };
      if (verification.score >= best.candidate.score || best.candidate.score < PERFECT && verification.score >= best.candidate.score - 5) {
        return {
          ...judged.menu,
          engine: { ...report, strategy: "judged-merge", judge: judgeInfo, finalScore: verification.score, finalIssues: verification.issues },
        };
      }
      // Judge made it worse; keep the best raw candidate but record what happened.
      return {
        ...best.menu,
        engine: { ...report, judge: { ...judgeInfo, rationale: `${judged.rationale} (Merge scored ${verification.score}, below the best candidate at ${best.candidate.score}, so the best candidate was kept.)` } },
      };
    } catch (error) {
      const message = controller.signal.aborted ? "Judge timed out." : error instanceof Error ? error.message : "Judge failed.";
      return {
        ...best.menu,
        engine: { ...report, judge: { provider: judge.id, model: judge.model, winner: best.provider.id, rationale: `Judge unavailable (${message}); best verified candidate used.`, mergedFrom: [best.provider.id], ms: Date.now() - started } },
      };
    } finally {
      clearTimeout(timer);
    }
  }

  return { ...best.menu, engine: report };
}
