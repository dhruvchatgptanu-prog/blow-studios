# Research snapshot (2026-10-03)

A small, real sample used to test the research pipeline (`tests/fixtures/research_sample_2026-10-03.json`).

**Provenance.** Retrieved on 2026-10-03 at about 03:45 UTC through the TubeAlfred connector (YouTube
search and video details), *not* through the YouTube Data API: query "roblox animation story shorts",
uploads from the last week, under three minutes. The items were converted to the Data API
`videos.list` shape for tests. Retrieval used paid connector credits, so collection was stopped after
one batch.

**Limits (read before drawing conclusions).**

* Twelve videos from one query: a tiny, biased sample, not "what is trending on YouTube".
* One snapshot only, so no measured velocity. The "views/day" column divides views by the time since
  the publication *date* (day precision) and is a rough indicator at best.
* Like counts of 0 were reported for several videos; they are treated as hidden/unavailable.
* Player sizes were unavailable, so vertical format is unknown; Blox therefore rates every item only as
  a *possible* Short (duration and hashtags are weak signals).
* No transcripts were available. Nothing below describes dialogue, scenes or visuals; only titles,
  durations and counts were observed.

| Title (truncated) | Channel | Length | Views at retrieval | Rough views/day | Blox Shorts label | Re-upload wording |
|---|---|---|---|---|---|---|
| FROM A MAID TO A PRINCESS, MY LIFE CHANGED #story #roblox … | lifeisluca | 2m15s | 137,664 | ~63,800 | possible (0.40) | |
| The "Girl Code" in action 💀 #shorts #roblox #animation | VOXIT | 31s | 96,927 | ~45,000 | possible (0.55) | |
| Police Officer Saved Her Life ❤️🚔 / Roblox Story #Shorts | USA WIN | 57s | 35,454 | ~11,200 | possible (0.55) | |
| Ranking Top 3 Most Funniest Roblox Animated Stories pt-6 … | Ztrocs | 2m21s | 33,751 | ~10,700 | possible (0.40) | yes |
| I should have ignored that call (ROBLOX LOVE STORY) ☎️💀 … | SANDBOX BABY | 45s | 32,085 | ~27,700 | possible (0.50) | |
| Bullies Made a Huge Mistake! Roblox Emotia Escape Inspired by … | Mirkino AI Comics | 1m00s | 26,852 | ~12,500 | possible (0.50) | |
| Ranking Top 3 Most Funniest Roblox Animated Stories pt-8 … | Ztrocs | 2m44s | 21,773 | ~18,800 | possible (0.40) | yes |
| Die With A Smile / He Saved Her Life🥺 / Roblox Edit … | Noa Universe | 17s | 12,085 | ~10,500 | possible (0.50) | |
| When your Bro always cheats in games 💀 #roblox #shorts | cartoon 3rd | 43s | 6,876 | ~3,200 | possible (0.55) | |
| Todd Saves a Fairy From His Own Sister! 😱🧚 / Roblox Story … | Roblox Gaming Studio | 45s | 5,500 | ~2,600 | possible (0.50) | |
| He Sacrificed His Life to Save the Princess 😢❤️ #roblox … | DrawnToonz | 1m23s | 4,817 | ~4,200 | possible (0.40) | |
| Me vs Bro in STEAL A BRAINROT / ROBLOX ANIMATION … | Toonzy | 35s | 4,474 | ~3,900 | possible (0.55) | |

**What the titles suggest (hypotheses, not findings).** Many titles frame an emotional turn or a rescue
("saved her life", "sacrificed", "from a maid to a princess"), a relatable sibling/friend rivalry
("me vs bro", "bro always cheats"), or a twist warning ("I should have ignored that call"). Emoji in titles
signal the intended emotion. Lengths range from 17 s to 2 m 44 s. These are prompts for original
stories, to be tested with your own analytics; they say nothing about why any video performed.

**Exclusions.** The two "Ranking Top 3 …" videos contain compilation wording, so Blox excludes them as
inspiration sources (they may reuse other creators' footage). One title credits another creator
("Inspired by @…"); Blox records such references but never reproduces a specific creator's story,
sequence, characters, voice or branding.

**Suggested next step.** Add the channels you consider relevant to the research watchlist (Trend
research → Add a reference), connect a YouTube Data API key, and let snapshots accumulate for a few days
so velocity becomes *measured* instead of estimated.
