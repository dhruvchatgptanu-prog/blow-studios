# Recovery status

**Resolved on 2026-10-30.** The two missing files (`src/lib/ai/coach.ts`,
`src/components/BriefCoach.tsx`) were written to Lovable verbatim in one message
(0.6 credits). Lovable's typecheck and preview build are clean again. The Lovable
project is now at: round 2 engines + round 3/4 `cuisine.ts` patch + brief coach.
Rounds 3-5 proper (model fallback chains, store wizard, templates, display fitting,
contrast guard, dietary compliance) are still only on GitHub.

## Getting the rest in without credits
The three bundles in `apply/` would cost roughly 20+ credits (round 1 cost 11.5,
round 2 cost 5.2). Connect the project to GitHub instead (free, permanent):

1. Lovable editor → project Settings → GitHub → Connect / Transfer to GitHub.
2. Tell Claude the repository name. Claude pushes the whole `src/` tree from
   `lovable/menu-master-ai/src/` plus the `@anthropic-ai/sdk` dependency; Lovable
   syncs the commit automatically and every future daily improvement is free.

## Original incident (for the record)
On 2026-10-26 the Lovable agent applied the two small edits of the round 3+4
message (`src/lib/ai/cuisine.ts` and `src/routes/index.tsx`) but stopped before
writing the four new files, then the workspace ran out of credits. The editor
preview therefore fails to build: `src/routes/index.tsx` imports
`@/components/BriefCoach`, which does not exist yet.

The **published** site is a separate deployment and is only updated when you
press Publish, so it should still be serving the previous working build.
Do not press Publish/Update until the preview builds again.

