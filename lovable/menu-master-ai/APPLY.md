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
| `src/lib/ai/intent.ts`, `src/lib/chat.ts`, `src/lib/images.ts`, `src/components/ChatComposer.tsx`, `src/components/ChatMessageView.tsx` | new (round 6) — the chat studio |
| `src/routes/index.tsx` | **replace** (round 6) — ChatGPT-style conversation is now the home page |
| `src/routes/studio.tsx` | new (round 6) — the previous form studio, kept at `/studio` |
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
* **Contrast guard (daily run 2)** — `color.ts` converts the OKLCH theme tokens to WCAG contrast
  ratios; every template is checked on the text/background pairs the sheet renders (ink on
  paper and card, poster text, prices, accent labels). Failing themes are never recommended,
  AAA themes get a bonus on distance-read surfaces and in dim rooms. Four themes were fixed.
  A size estimate from a photo with no named reference object is capped at 30% confidence.
  `eval/color.test.mjs`.
* **Priority-aware fitting + digital board rules (daily run 3)** — when a display cannot hold
  every item, `fitMenuToDisplay` now drops the least important items first instead of the last
  ones: store-profile signature dishes, items the brief marks as signature/best seller/special,
  items the owner named at all, badged items, then the first item of each section. Sections are
  kept by the importance of their best item and rendered in original order. The store wizard now
  passes the profile's `signatureItems` to the engines ("keep them, give them the hero spots") and
  to the fitter. Digital screens get a dwell-time budget (max 9 items and 3 sections per screen,
  from the 15-30 s QSR decision window) and a `DIGITAL SCREEN RULES` prompt block (thick-stroke
  sans, 7:1 contrast, no hairlines or fine textures, no large white fields). The unsupported
  "golden triangle" rule was replaced by the evidence-backed book-order / first-and-last-item rule.
  Tests added to `eval/store-display.test.mjs`; `apply/regenerate.mjs` refreshes the bundles.
* **Dietary markers, allergen statements and anti-CGI photo prompts (daily run 4)** — the verifier
  now treats dietary information as legal text: markers the brief attaches to an item ((V), (VG),
  (GF), (DF), (NF), (H), (K)) must survive on that item, dietary claims the brief never makes
  (vegan, gluten-free, halal, organic…) are flagged as invented, and a brief that gives an allergen
  statement must see it carried in the footer. Section headers and legal lines ("Mains:",
  "Allergens:") are no longer mistaken for dish names. Photo prompts lose CGI buzzwords ("8k",
  "ultra-realistic", "hyper-detailed", "flawless", "masterpiece") deterministically in enrichment
  and are penalised by the verifier when an engine writes them; the realism cue gains "true-to-life
  saturation, natural highlights, no waxy sheen". The brief coach notes dietary markers. Tests in
  `eval/verifier.test.mjs`.
* **Memory status, wider fallback chains, evidence-based label rule (daily run 6)** — the learning
  memory no longer fails silently: every generation reports whether it was saved and, if not, why
  (`off`, `unavailable: missing SUPABASE_…`, `timeout`, `failed: <database error>`), and the Engines
  panel shows "Memory · not saved (reason)". The OpenAI and gateway fallback chains gain the
  `gpt-6-luna` and `gpt-5.6-terra` ids OpenAI now names as replacements. The descriptive-label rule
  now cites the Wansink field study and asks for one label of a proven type (geographic, sensory,
  nostalgic, named ingredient) rather than longer copy. Tests in `eval/ensemble.test.mjs`.
* **Four more cuisines (daily run 7)** — Thai, Vietnamese, charcoal chicken / Portuguese and pub /
  bistro classics now have their own detection keywords, plating cues (overhead flat lay and
  banana leaf for Thai; the cut face of a banh mi and herbs on the pho rim; char lines and glossy
  peri peri glaze on a kraft-lined tray; parmigiana with chips and salad on one white plate) and
  photo defaults, and the matching templates list them. Thai and pho no longer fall under the
  generic "chinese / asian" bucket. The no-key store profile now guesses the cuisine from the
  description so template recommendations work before any engine runs. Four briefs added to
  `eval/eval.mjs`, one check to `eval/store-display.test.mjs`.
* **First live data (daily run 8)** — three real generations showed two defects. (1) Lovable still
  scored candidates before enrichment, so every generation paid a repair call (~10 s) that only
  fixed prompt length; a FIND/REPLACE patch brought enrich-before-scoring to Lovable. (2) Classic
  menus leave item photo prompts empty and enrichment padded them into subjectless prompts; the
  enricher now starts an empty or too-short prompt with the dish name and description (menu level:
  item name + description, dish photos: label, hero: venue signature dishes) and the verifier
  flags any item prompt that never names the dish. The brief coach now warns when the venue name
  will print with odd capitals ("Burger oclock") because names are reproduced exactly as typed.
* **Placeholder prices (daily run 9)** — a brief that allows invented prices ("you can use random
  prices", "prices up to you", "sample pricing") is now recognised: the coach stops demanding exact
  prices and explains that placeholders will be marked; the knowledge base asks for realistic prices
  with one consistent ending style and a 'Prices are placeholders, replace before printing' line in
  'followed'; the verifier checks both (mixed endings or an undeclared placeholder set cost 3 points
  each). Evidence: price-ending research finds 9/90 endings read as value and round endings as
  quality, so mixing them on one menu sends both signals at once. Tests in `eval/verifier.test.mjs`
  and `eval/coach.test.mjs`.

## Round 6 (chat studio)

* The home page is a conversation, like ChatGPT: a sidebar of saved designs, a thread of
  messages, and a message box with attachment and format chips. The owner types the brief in
  plain words; Halftone replies with the finished design rendered in the thread plus a short
  summary (what was kept, the verifier score, which engines ran); the owner refines it by typing
  ("make the prices bigger", "swap the second offer for a kids meal $12", "turn it into a wide
  board"), asks for photos ("create the photos") or attaches a food photo to improve. Every
  revision folds into the running brief so exact names and prices keep being verified.
* `src/lib/ai/intent.ts` decides deterministically whether a message is a new design, a
  revision, a photo request or a question, and reads mode / format / style / page count from
  the words; `src/lib/chat.ts` stores conversations in localStorage (photos stay in memory).
* Printing works per design: the chosen sheet keeps the print class, everything else is hidden.
* The form studio survives at `/studio`; the store wizard stays at `/setup` and its template,
  display and venue profile feed the chat too.
* Delivery to Lovable no longer pastes files: the agent fetches them from GitHub with
  `apply/fetch-from-github.sh` (see `apply/README.md`).
* Tests: `eval/intent.test.mjs`.

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
| `OPENAI_FALLBACK_MODELS` / `ANTHROPIC_FALLBACK_MODELS` / `LOVABLE_FALLBACK_MODELS` | optional | comma-separated model ids tried in order when the primary model id is retired or unknown (defaults: `gpt-6.1-sol,gpt-6-sol,gpt-6-luna,gpt-5.6-sol,gpt-5.6-terra,gpt-5.5` / `claude-sonnet-5-5,claude-opus-5` / `openai/gpt-6.1-sol,openai/gpt-6-sol,openai/gpt-6-luna,openai/gpt-5.6-sol`) |

With only `LOVABLE_API_KEY` set the app behaves exactly like before plus the
verifier and the Engines panel. Each extra key adds an engine automatically.

## Local verification done here

* `tsc --strict` over `src/lib/ai/*` against `@anthropic-ai/sdk@1.3.0` and `zod@3.25.76`: clean.
* Verifier behaviour test on the sample Yiros House brief: exact-price, exact-name,
  invented-phone-number and photo-hygiene checks all trigger as intended.
