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
| `src/lib/ai/coach.ts` | new (round 4) — rule-based brief coach |
| `src/components/BriefCoach.tsx` | new (round 4) — hints under the brief textarea |
| `src/lib/ai/store.ts` | new (round 5) — store profile schema, store-type design guide, text baseline |
| `src/lib/ai/templates.ts` | new (round 5) — 16-template library with scoring and sheet themes |
| `src/lib/ai/display.ts` | new (round 5) — display measurement schema, geometry, legibility budget, menu fitting |
| `src/lib/ai/store.server.ts` | new (round 5) — vision analysis of store photos and display photos |
| `src/lib/store.functions.ts` | new (round 5) — `analyzeStore` and `measureDisplay` server functions |
| `src/lib/setup.ts` | new (round 5) — wizard → studio hand-off |
| `src/components/StoreWizard.tsx`, `src/routes/setup.tsx` | new (round 5) — the three-step wizard page at `/setup` |
| `src/components/MenuSheet.tsx` | **replace** (round 5) — template themes and custom display sizes; layouts otherwise unchanged |
| `src/routes/index.tsx` | **replace** (round 5) — picks up the wizard result, sends template/display/store context |
| `src/routeTree.gen.ts` | **replace** (round 5) — adds `/setup`; the router plugin regenerates it anyway |
| `src/lib/ai/schema.ts`, `verify.ts`, `memory.server.ts`, `providers.server.ts`, `ensemble.server.ts`, `menu.functions.ts` | **replace** (round 5) |
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
5. (round 4) Add `import { BriefCoach } from "@/components/BriefCoach";` and place
   `<BriefCoach brief={brief} mode={mode} />` directly after the brief `<textarea … />`.

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
* **Model fallback chains (round 3)** — every engine tries its configured model, then a
  list of fallbacks, when the API says the model id is unknown, retired or not permitted.
  Other errors are never retried. The Engines panel shows the model that actually answered.
* **Enrich before scoring (round 4)** — candidates, judge output and repair output are
  photo-enriched before the verifier scores them, so no judge or repair call is spent on
  prompt length that the free enrichment fixes anyway. Enrichment is idempotent.
* **Brief coach (round 4)** — instant rule-based hints under the brief (missing venue,
  prices, offer names, counts, sections) using the same extractors as the verifier.
* **Eval harness** — `eval/eval.mjs` runs six briefs through detection,
  extraction, enrichment and the verifier; `eval/ensemble.test.mjs` runs the real
  orchestration against a mocked gateway (judge merge, repair, model fallback, total
  failure, malformed JSON); `eval/fallback.test.mjs` and `eval/coach.test.mjs` cover
  model-error detection and the brief coach. All pass.

## Round 5 (store → template → display)

* **Store analysis** — `/setup` step 1: description plus up to four photos → a structured
  StoreProfile (type, cuisine, price tier, order flow, brand colours, lighting, display
  surfaces, signature items) from a vision engine, merged with a deterministic text baseline
  so it works with no key at all.
* **Templates** — 16 templates (Street Deals, Charcoal Grill, Pizzeria Classic, Kraft Café,
  Botanical Brunch, Chalkboard Pub, Noir Fine, Sushi Slate, Spice Market, Taquería Pop,
  Smash House, Harbour Fish, Patisserie Blush, Screen Grid, Truck Bold, Mono Modern), each
  with a renderer style, colour theme, typography, layout grammar and photo treatment, scored
  against the profile with plain-language reasons.
* **Display measurement** — step 3: a photo of the board/screen/window → display type,
  corners, physical size estimated from reference objects (doors, A4 sheets, bricks, TVs,
  counters…), viewing distance and lighting. Deterministic geometry corrects perspective,
  maps to the nearest known format or a custom size, and derives a legibility budget
  (minimum letter height from viewing distance → max items/offers/sections, name and
  description word limits, photo count). The owner can type one or both dimensions instead.
* **Fitting** — the engines receive the display budget, template and venue profile in the
  prompt; the verifier scores against the budget; `fitMenuToDisplay` enforces it after the
  judge and lists what was omitted. The sheet renders custom sizes with the right layout family
  and prints at the real size. Templates re-colour the sheet through CSS variables; the photo
  pipeline is untouched.
* **Database** — `halftone_stores` and `halftone_displays` tables (created, RLS on); generations
  link to them via `store_id`, `display_id`, `template_id`.
* **Tests** — `eval/store-display.test.mjs` (profiles, templates, geometry, budgets, fitting).

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
| `OPENAI_FALLBACK_MODELS` / `ANTHROPIC_FALLBACK_MODELS` / `LOVABLE_FALLBACK_MODELS` | optional | comma-separated model ids tried in order when the primary model id is retired or unknown (defaults: `gpt-6.1-sol,gpt-6-sol,gpt-5.6-sol,gpt-5.5` / `claude-sonnet-5-5,claude-opus-5` / `openai/gpt-6.1-sol,openai/gpt-6-sol,openai/gpt-5.6-sol`) |

With only `LOVABLE_API_KEY` set the app behaves exactly like before plus the
verifier and the Engines panel. Each extra key adds an engine automatically.

## Local verification done here

* `tsc --strict` over `src/lib/ai/*` against `@anthropic-ai/sdk@1.3.0` and `zod@3.25.76`: clean.
* Verifier behaviour test on the sample Yiros House brief: exact-price, exact-name,
  invented-phone-number and photo-hygiene checks all trigger as intended.
