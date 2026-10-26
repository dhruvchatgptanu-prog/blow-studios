# Recovery: finish the round 3+4 apply (Lovable ran out of credits mid-way)

## What happened
On 2026-10-26 the Lovable agent applied the two small edits of the round 3+4
message (`src/lib/ai/cuisine.ts` and `src/routes/index.tsx`) but stopped before
writing the four new files, then the workspace ran out of credits. The editor
preview therefore fails to build: `src/routes/index.tsx` imports
`@/components/BriefCoach`, which does not exist yet.

The **published** site is a separate deployment and is only updated when you
press Publish, so it should still be serving the previous working build.
Do not press Publish/Update until the preview builds again.

## Fix option A (needs a few credits, 1 message)
Add credits, open the project chat and send exactly:

    continue — write the 4 PART A files exactly as given in my previous message
    (src/lib/ai/providers.server.ts, src/lib/ai/ensemble.server.ts,
    src/lib/ai/coach.ts, src/components/BriefCoach.tsx), verbatim, no changes.
    Then run the type check and confirm the build is clean.

The agent still has the file contents in its thread.

## Fix option B (free, no credits): connect GitHub
1. In the Lovable editor: project Settings → GitHub → Connect / Transfer to GitHub.
   Lovable creates a repository that mirrors the project and syncs both ways.
2. Tell Claude the repository name. Claude pushes these four files into it:
   - `lovable/menu-master-ai/src/lib/ai/providers.server.ts`
   - `lovable/menu-master-ai/src/lib/ai/ensemble.server.ts`
   - `lovable/menu-master-ai/src/lib/ai/coach.ts`
   - `lovable/menu-master-ai/src/components/BriefCoach.tsx`
   at the same paths (without the `lovable/menu-master-ai/` prefix), and Lovable
   picks them up automatically. Every future improvement then costs no credits.

## Fix option C (free, quickest to unbreak): revert in the editor
In the editor's version history, restore the version before
"Round 3+4" (the one titled around "Round 2 upgrade"). That removes the
BriefCoach import and the preview builds again; rounds 3 and 4 can be applied
later with option A or B.
