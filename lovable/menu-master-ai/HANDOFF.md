# Handoff — what was done to Menu Master AI (Halftone)

## In one paragraph
Halftone now composes every menu with up to three AI engines at once (its own
built-in Lovable engine, ChatGPT and Claude), checks each result against your
brief with a rule-based verifier that cannot hallucinate, and lets a judge model
merge the strongest parts into the final design. The food photographs are
produced by exactly the same model, size, quality and photo direction as
before, so the realistic look is unchanged; the only photo change is that the
per-dish photo prompts are now written to a stricter, more detailed recipe.

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
