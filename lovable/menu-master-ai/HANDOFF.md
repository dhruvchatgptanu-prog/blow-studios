# Handoff — what was done to Menu Master AI (Halftone)

## In one paragraph
Halftone now composes every menu with up to three AI engines at once (its own
built-in Lovable engine, ChatGPT and Claude), checks each result against your
brief with a rule-based verifier that cannot hallucinate, and lets a judge model
merge the strongest parts into the final design. The food photographs are
produced by exactly the same model, size, quality and photo direction as
before, so the realistic look is unchanged; the only photo change is that the
per-dish photo prompts are now written to a stricter, more detailed recipe.

## Round 2 (added later the same day)
Halftone now learns from every design it makes. Each generation is recorded in
the project database with its scores; past designs that scored 100/100 for the
same kind of menu and cuisine are shown to the engines as approved examples,
and the judge model with the best track record is promoted automatically. When
the verifier still finds a problem after the judge, one extra proofreading pass
fixes exactly that problem. Cuisine is detected from the brief (17 cuisines) so
the photo prompts get the right plating, surface and lighting defaults, and
every photo prompt is completed deterministically with realism cues. The photo
model and photo direction are still untouched.

A daily scheduled task ("Halftone daily research and improvement") now runs in
this Claude session: it reads the generation data, researches, improves the
code on GitHub, and sends Lovable at most one apply message per day and only
when the change is substantive. To stop it, tell Claude "stop the daily
Halftone task" or disable it in your scheduled tasks list.

## Rounds 3 and 4
Every engine now has a model fallback chain, so a retired model name cannot
take the app down. Candidates are photo-enriched before they are scored, which
avoids paying for judge or repair calls that only fix prompt length. A brief
coach under the textarea tells you instantly what the brief is missing (venue,
prices, offer names, counts, sections) before any AI runs. The whole
orchestration is now covered by a mocked end-to-end test.

## Round 5 (store setup wizard)
A new page at `/setup` does what you asked for: describe the store and add photos,
Halftone analyses the venue (type, cuisine, price point, brand colours, light, where
menus are displayed) and recommends four templates with reasons. Pick one, paste the
current menu, photograph the display, and Halftone estimates its real size from
objects in the photo (or you type one dimension), works out how big the letters must
be from where customers stand, and fits the menu to that board: right orientation,
right number of items, shorter copy where needed, and a list of anything left out.
The studio then designs with that template, size and venue profile, and prints at the
real dimensions. Not yet applied to Lovable (no credits); ready-to-paste bundles are in
`lovable/menu-master-ai/apply/`.

## Daily runs so far
Run 2 added a colour-contrast guard so no template can recommend unreadable text. Run 3 made the
display fitter choose what to cut intelligently (your signature dishes and anything you call a
best seller or special always survive; filler goes first), taught the engines the rules for
digital screens (at most nine items per screen, thick type, high contrast) and corrected a menu
"psychology" rule that research does not support. Run 4 made dietary markers and allergen lines
legally safe: markers like (V), (GF) stay on the dish they belong to, no diet or allergen promise
is ever invented, and an allergen statement in your brief is always printed in the footer. Photo
prompts also drop "8k / ultra-realistic / flawless" wording, which makes AI food look like plastic.
On 30 Oct the broken Lovable preview was repaired with one 0.6-credit message
(the two missing brief-coach files). Nothing else has gone to Lovable since round 2
because the workspace has no credits; the apply bundles in `apply/` stay current automatically.

## What you need to do (5 minutes)
1. Open the Lovable editor for Menu Master AI and add two Cloud secrets:
   `OPENAI_API_KEY` (from platform.openai.com) and `ANTHROPIC_API_KEY`
   (from console.anthropic.com). The Lovable agent was asked to prompt for
   them; if it paused on a secure form, finish it in the editor.
2. Generate the sample Yiros House poster. The new **Engines** panel under the
   brief shows each engine's score and the judge's note.
3. Optional: if OpenAI rejects the default model name, set the secret
   `OPENAI_MODEL` to a model you have access to. Likewise `ANTHROPIC_MODEL`.

## Where things live
- GitHub (this repo): `lovable/menu-master-ai/` — the exact files plus
  `APPLY.md` (file-by-file apply guide).
- Lovable project knowledge: updated with the photo style lock, the engine
  architecture and the content rules, so future Lovable prompts respect them.
- Pull request: see the branch `claude/quirky-ride-sbvze7`.

## Credits
Only one Lovable message was sent (the apply message). Reading the project,
setting project knowledge and everything on GitHub cost no Lovable credits.
