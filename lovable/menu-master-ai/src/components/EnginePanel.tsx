import { Bot, CheckCircle2, Scale, XCircle } from "lucide-react";

import type { EngineReport } from "@/lib/menu.functions";

const LABELS: Record<string, string> = { lovable: "Halftone built-in", openai: "ChatGPT", claude: "Claude" };

function scoreTone(score: number) {
  if (score >= 90) return "text-herb";
  if (score >= 70) return "text-press";
  return "text-destructive";
}

/**
 * Shows which engines composed the current design, how each scored on the
 * deterministic brief-compliance check, and what the judge kept or fixed.
 */
export function EnginePanel({ engine }: { engine: EngineReport | undefined }) {
  if (!engine) return null;
  const strategy = engine.strategy === "judged-merge" ? "Merged by judge" : engine.strategy === "best-candidate" ? "Best candidate kept" : "Single engine";
  return (
    <section className="mt-6 border-l-4 border-ink bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2"><Bot className="size-4 text-ink" /><p className="font-mono text-[10px] font-bold uppercase text-ink">Engines</p></div>
        <p className="font-mono text-[9px] font-bold uppercase text-muted">{strategy} · <span className={scoreTone(engine.finalScore)}>{engine.finalScore}/100</span></p>
      </div>
      <ul className="mt-3 space-y-2">
        {engine.candidates.map((candidate) => (
          <li key={candidate.provider} className="grid grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-2 text-xs">
            {candidate.status === "ok" ? <CheckCircle2 className="mt-0.5 size-4 text-herb" /> : <XCircle className="mt-0.5 size-4 text-destructive" />}
            <div className="min-w-0">
              <p className="truncate font-semibold">{LABELS[candidate.provider] ?? candidate.provider} <span className="font-mono text-[9px] font-normal uppercase text-muted">{candidate.model}</span></p>
              {candidate.status === "ok"
                ? candidate.issues.length > 0 ? <p className="mt-0.5 text-[11px] leading-snug text-muted">{candidate.issues[0]}{candidate.issues.length > 1 ? ` (+${candidate.issues.length - 1} more)` : ""}</p> : <p className="mt-0.5 text-[11px] text-muted">Brief followed exactly.</p>
                : <p className="mt-0.5 text-[11px] leading-snug text-destructive">{candidate.error}</p>}
            </div>
            <span className={`font-mono text-[10px] font-bold ${candidate.status === "ok" ? scoreTone(candidate.score) : "text-muted"}`}>{candidate.status === "ok" ? `${candidate.score}` : "—"}<span className="ml-1 font-normal text-muted">{(candidate.ms / 1000).toFixed(0)}s</span></span>
          </li>
        ))}
      </ul>
      {engine.judge ? (
        <div className="mt-3 border-t border-line pt-3">
          <div className="flex items-center gap-2"><Scale className="size-3.5 text-muted" /><p className="font-mono text-[9px] font-bold uppercase text-muted">Judge · {LABELS[engine.judge.provider] ?? engine.judge.provider} · base {LABELS[engine.judge.winner] ?? engine.judge.winner}</p></div>
          <p className="mt-1 text-[11px] leading-snug text-muted">{engine.judge.rationale}</p>
        </div>
      ) : null}
      {engine.repair ? (
        <p className="mt-2 font-mono text-[9px] font-bold uppercase text-muted">Repair pass · {LABELS[engine.repair.provider] ?? engine.repair.provider} · {engine.repair.applied ? `${engine.repair.before} → ${engine.repair.after}` : "no improvement, original kept"}</p>
      ) : null}
      <p className="mt-2 font-mono text-[9px] font-bold uppercase text-muted">
        {engine.cuisine.length ? `Cuisine · ${engine.cuisine[0]}` : "Cuisine · general"} · Memory · {engine.memory.totalRecorded} designs learned{engine.memory.exemplars ? `, ${engine.memory.exemplars} shown as examples` : ""} · {(engine.totalMs / 1000).toFixed(0)}s total
      </p>
      {engine.finalIssues.length > 0 ? (
        <div className="mt-3 border-t border-line pt-3">
          <p className="font-mono text-[9px] font-bold uppercase text-destructive">Still check</p>
          <ul className="mt-1 space-y-1">{engine.finalIssues.slice(0, 4).map((issue) => <li key={issue} className="text-[11px] leading-snug text-muted">• {issue}</li>)}</ul>
        </div>
      ) : null}
    </section>
  );
}
