import { Lightbulb, TriangleAlert } from "lucide-react";
import { useMemo } from "react";

import { coachBrief } from "@/lib/ai/coach";

/** Instant, rule-based hints under the brief. Runs in the browser; no AI, no credits. */
export function BriefCoach({ brief, mode }: { brief: string; mode: "menu" | "poster" }) {
  const report = useMemo(() => coachBrief(brief, mode), [brief, mode]);
  if (report.hints.length === 0) return null;
  const fixes = report.hints.filter((hint) => hint.level === "fix");
  return (
    <div className="mt-2 border-l-2 border-line pl-3">
      <p className="font-mono text-[9px] font-bold uppercase text-muted">Brief check · {report.score >= 85 ? "ready" : fixes.length ? "needs detail" : "could be sharper"}</p>
      <ul className="mt-1 space-y-1">
        {report.hints.map((hint) => (
          <li key={hint.text} className="flex items-start gap-1.5 text-[11px] leading-snug text-muted">
            {hint.level === "fix" ? <TriangleAlert className="mt-0.5 size-3 shrink-0 text-press" /> : <Lightbulb className="mt-0.5 size-3 shrink-0 text-herb" />}
            <span>{hint.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
