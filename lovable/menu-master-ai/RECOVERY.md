# Recovery status

**Lovable is at parity with GitHub commit `4fd2297` (6 Nov 2026).** Blocker on the live app: the Lovable AI gateway reports "Not enough credits"; the owner's own API keys (GEMINI_API_KEY free, OPENAI_API_KEY, ANTHROPIC_API_KEY) bypass it. The whole upgrade (rounds
3-6, including the chat home page) was pulled into Lovable with one message that ran
`apply/fetch-from-github.sh` (1 credit; typecheck clean). Future daily runs can use the same
script with a newer `HALFTONE_SHA` whenever a change is worth a credit.


If Lovable ever drifts again, the cheapest path is the fetch script above; a GitHub connection in
the Lovable editor would make it free:

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

