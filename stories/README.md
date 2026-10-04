# Story batches

Original stories for the recurring cast (Bloxy and Pip), written as complete director's plans that the
studio can produce without any AI API. Each file is a JSON list of plans in the same format as the
**Plan JSON** tab of the Director's editor.

| File | Stories | Written by | Notes |
|---|---|---|---|
| `batch-2026-10-03-claude.json` | 12 (about one day at 12 per day) | Claude, in a chat session on 2026-10-03 | All pass the validator and the originality screen against each other and the demo story. Themes were informed only by broad patterns in research titles (twists, trades, rivalry, rescues); no specific video was used. |

## Import

In the studio: **Story backlog → paste the file contents → Validate and add**. Or from a shell:

```bash
python -m blox.cli import-stories stories/batch-2026-10-03-claude.json --source claude
```

Every plan is compiled and validated again on import (invalid or duplicate plans are reported and
skipped), and screened for originality against research references and your recent videos before it
is produced. With no LLM API key connected, autopilot uses the backlog automatically, oldest story
first. The dashboard shows how many days of stories are left.

## Getting more stories from Claude (no API cost)

Paste this into a Claude chat, together with one existing story from this folder as the example:

> Write N original 30-second stories for my animated Shorts channel as a JSON list, in exactly the same
> format as the example plan. Use only the two cast members `bloxy` (ch_bloxy: bright, energetic,
> slightly overconfident) and `pip` (ch_pip: calm, dry, teasing best friend). Use only these
> settings: sky_obby, lava_obby, town_street, classroom, night_forest, bedroom, studio, and only the
> props, actions, expressions, arm poses, camera framings and sound cues that appear in the example
> (the full list is at `/api/vocabulary` in the studio). Every story needs a hook in the first 3
> seconds, a payoff in the last quarter, beats for every second, shots that cover 0-30 s with no gaps,
> lines short enough to say in their time window, and characters standing on ground (platforms in obby
> settings). Kind, funny, safe for a general audience; no real people or brands; never claim it is
> real gameplay. Make each story different from the others and from the example.

Then import the result as above. The validator will tell you exactly what to fix if a plan does not
fit (for example a line that is too long for its window); you can paste those messages back to Claude.
