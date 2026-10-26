# Menu Master AI (Halftone) — multi-engine upgrade

This folder mirrors the files that change inside the Lovable project
**Menu Master AI** (`https://lovable.dev/projects/57a60bc5-5dd9-4d87-afb7-af99fc8e420e`).
Everything here was written and type-checked outside Lovable so that Lovable
only has to copy files in (one message, minimal credits).

## What it does

```
brief ─┬─► Halftone built-in  (Lovable AI gateway, LOVABLE_API_KEY)  ─┐
       ├─► ChatGPT            (OpenAI Responses API, OPENAI_API_KEY) ├─► verifier ─► judge (merge + fix) ─► verifier ─► best menu
       └─► Claude             (Anthropic SDK, ANTHROPIC_API_KEY)      ┘
```

* All three engines run **at the same time** on the same brief, same knowledge
  base, same JSON contract.
* A **deterministic verifier** (no AI) scores every candidate 0-100: exact prices,
  exact dish names, deal quantities restated, photo-prompt hygiene, structure,
  no invented phone numbers or claims.
* The **judge** (Claude by default, then ChatGPT, then built-in) receives every
  candidate plus its verifier report and produces one merged menu that fixes the
  listed issues. The merge is kept only if it verifies at least as well as the
  best raw candidate.
* Any engine may be missing or fail; the app always returns the best menu it
  could produce and shows what happened in a new **Engines** panel.

## Photo style is untouched

The photographs keep the exact look they have today. These files are **not**
modified: `src/lib/image-gateway.server.ts`, `src/routes/api/generate-image.ts`,
`src/routes/api/edit-image.ts`, `src/lib/streamImage.ts`. The only photo change
is that the text engines now write richer, more precise per-dish photo prompts
(subject → plating → surface → angle → light → finish) which the existing
`PHOTO_DIRECTION` suffix still styles.

## Files

| Path in Lovable project | Action |
|---|---|
| `src/lib/ai/schema.ts` | new |
| `src/lib/ai/knowledge.ts` | new — the knowledge base (brief rules, format rules, menu psychology, cuisine plating cues, photo grammar) |
| `src/lib/ai/verify.ts` | new — deterministic verifier |
| `src/lib/ai/providers.server.ts` | new — Lovable gateway / OpenAI / Claude adapters |
| `src/lib/ai/ensemble.server.ts` | new — parallel run + judge |
| `src/lib/menu.functions.ts` | **replace** (same `generateMenu` export, same input shape) |
| `src/components/EnginePanel.tsx` | new |
| `src/lib/ai/cuisine.ts` | new (round 2) — deterministic cuisine detection + photo-prompt enrichment |
| `src/lib/ai/memory.server.ts` | new (round 2) — learning memory on the `halftone_generations` table |
| `src/routes/index.tsx` | 4 small edits, see below |
| `package.json` | add dependency `@anthropic-ai/sdk` (zod ^3.25 already satisfies its peer dep via `zod/v4`) |

### Edits to `src/routes/index.tsx`

1. Import line — replace
   `import { generateMenu, type GeneratedMenu } from "@/lib/menu.functions";`
   with
   `import { generateMenu, stripEngine, type GeneratedMenu } from "@/lib/menu.functions";`
   and add
   `import { EnginePanel } from "@/components/EnginePanel";`
2. In `mutationFn`, replace `previous: JSON.stringify(menu)` with
   `previous: JSON.stringify(stripEngine(menu))`.
3. Button label: replace the two pending strings
   `"Reading and redesigning…"` / `"Composing your design…"` with
   `"Reading and redesigning with every engine…"` / `"Composing with every engine…"`.
4. Directly after the `{menu.followed.length > 0 ? <section …Instructions followed… /section> : null}` block in the aside, add
   `<EnginePanel engine={menu.engine} />`.

## Round 2 (learning + repair)

* **Learning memory** — every generation is recorded in the Supabase table
  `halftone_generations` (already created, RLS on, server-only). Past menus that
  scored 100/100 for the same mode and cuisine are shown to the engines as
  approved examples; the judge model with the best verified track record is
  promoted automatically once it has 5+ samples.
* **Repair pass** — when the verifier still lists issues after the judge, one
  targeted proofreading call fixes only those issues; kept only if the score rises.
* **Cuisine detection** — weighted keyword detection over 17 cuisines focuses the
  plating cues the engines apply and selects photo defaults.
* **Photo-prompt enrichment** — deterministic post-processing guarantees every
  prompt has surface, angle, light, focus, realism cues (deliberate imperfection,
  no fake steam, no non-food props) and the right composition line. The image
  model and PHOTO_DIRECTION are still untouched.
* **Knowledge updates from research** — signage legibility rules of thumb
  (letter height per viewing distance, sans-serif, no script for prices), serial
  position, descriptive labels, strict currency-format fidelity, 5-7 items per section.
* **Eval harness** — `eval/eval.mjs` runs six briefs through detection,
  extraction, enrichment and the verifier. All pass.

## Secrets (Lovable Cloud → Secrets)

| Name | Required | Default |
|---|---|---|
| `LOVABLE_API_KEY` | already present | — |
| `OPENAI_API_KEY` | for ChatGPT engine | — |
| `ANTHROPIC_API_KEY` | for Claude engine | — |
| `OPENAI_MODEL` | optional | `gpt-6-astra` |
| `ANTHROPIC_MODEL` | optional | `claude-opus-5-5` |
| `LOVABLE_TEXT_MODEL` | optional | `openai/gpt-6-astra` |
| `HALFTONE_JUDGE` | optional | `claude` (then `openai`, then `lovable`) |
| `HALFTONE_GENERATE_TIMEOUT_MS` / `HALFTONE_JUDGE_TIMEOUT_MS` / `HALFTONE_REPAIR_TIMEOUT_MS` | optional | `110000` / `90000` / `60000` |
| `HALFTONE_MEMORY` | optional | set to `off` to disable the learning memory |

With only `LOVABLE_API_KEY` set the app behaves exactly like before plus the
verifier and the Engines panel. Each extra key adds an engine automatically.

## Local verification done here

* `tsc --strict` over `src/lib/ai/*` against `@anthropic-ai/sdk@1.3.0` and `zod@3.25.76`: clean.
* Verifier behaviour test on the sample Yiros House brief: exact-price, exact-name,
  invented-phone-number and photo-hygiene checks all trigger as intended.
