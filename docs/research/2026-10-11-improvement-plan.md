# Blox Studio: ranked improvement plan (synthesis, 2026-10-11)

## 0. Provenance, tools, and what this plan could not use

**Date:** 2026-10-11. **Role:** synthesiser. **Connector calls: 0** (TubeAlfred 0, vidIQ 0, ThoughtLeaders YouTube 0). **WebSearch 0, WebFetch 0** (no gap needed closing that the web could close: the three reports and the verifier established that this sandbox cannot open any external page). **Local reads:** 4 `Read` calls + 7 `Bash` read/compute calls on:

- `/tmp/claude-0/-home-user-blow-studios/94335ae1-b0bf-5f99-ba6d-4ddeb4059494/scratchpad/mainstream_style_guide.md`
- `/tmp/claude-0/-home-user-blow-studios/94335ae1-b0bf-5f99-ba6d-4ddeb4059494/scratchpad/direction_brief.md`
- `/tmp/claude-0/-home-user-blow-studios/94335ae1-b0bf-5f99-ba6d-4ddeb4059494/scratchpad/trends_now.md`
- `/tmp/claude-0/-home-user-blow-studios/94335ae1-b0bf-5f99-ba6d-4ddeb4059494/scratchpad/agents/stories/writerA/plans.json` (all 6 plans measured; plans 0 and 1 read in full)
- `/home/user/blow-studios/blox/manifest/schema.py`, `/home/user/blow-studios/blox/manifest/validate.py` (hook rules), `/home/user/blow-studios/blox/prefs.py`, `/home/user/blow-studios/blox/learning.py`, `/home/user/blow-studios/blox/research/service.py`, `/home/user/blow-studios/blox/research/ranking.py`, `/home/user/blow-studios/blox/youtube/analytics.py`, `/home/user/blow-studios/blox/story/originality.py`, `/home/user/blow-studios/blox/web/app.py` (research routes), `/home/user/blow-studios/stories/README.md`, `/home/user/blow-studios/README.md` §6-8, `/home/user/blow-studios/docs/RESEARCH_SNAPSHOT.md`.

**Evidence quality, stated once:** every external fact in the three reports is search-snippet-only (SS) or comes from the 2026-10-03 vidIQ/TubeAlfred descriptions of the two reference Shorts (REF). The verifier confirmed the sandbox egress block (CONNECT 403 on a neutral control host) and could not verify the TubeAlfred 402 event or the PanoHadel channel identity/45.6K figure. No page was opened by anyone in this run. All "why" statements below are therefore hypotheses to test against the channel's own analytics; I tag inferences [HYP]. Nothing below reuses any creator's plot, lines, characters, catchphrases, branding, thumbnails or voice.

**Owner-requested inputs that were not reachable from this session (flagged, not faked):**
- The Chrome extensions' gathered data, the Claude chats "Viral Roblox brain rot video" and "Minecraft economics video series", and the connected YouTube channels. No tool in this session can read Claude chats or extension storage; the ThoughtLeaders YouTube Data connector needs OAuth authorisation in claude.ai connector settings; TubeAlfred failed to connect (proxy 403) and is out of credits; vidIQ was forbidden. Item R6 below adds an import path so this material can be used next run, and the owner-decision list says what to export.
- "hadelfilms" matched nothing in any index; the channel behind the reference Shorts is `@PanoHadel`. Owner should confirm that is the creator they mean.

**Where the newest stories stand (measured from `writerA/plans.json`, converted to finished time at the default production pace 1.5):**

| Plan | Finished length | Words | Words/s finished | Avg shot (finished) | Narration share | SFX/min | First word |
|---|---|---|---|---|---|---|---|
| Put on Trial Over ONE Block | 53.3 s | 151 | 2.8 | 1.57 s | 42 % | 22 | 0.13 s |
| Lies Make You Hop | 29.4 s | 69 | 2.3 | 1.40 s | 19 % | 33 | 0.13 s |
| Not Scary at All | 47.2 s | 130 | 2.8 | 1.75 s | 45 % | 19 | 0.13 s |
| Whoever Laughs Loses | 30.0 s | 68 | 2.3 | 1.36 s | 0 % | 32 | 0.13 s |
| My Loudest Fan | 47.3 s | 116 | 2.5 | 1.58 s | 53 % | 20 | 0.13 s |
| One Button, Ten Seconds | 30.0 s | 73 | 2.4 | 1.36 s | 0 % | 44 | 0.13 s |

Hook timing, cut rate, SFX density, titles and ending mix already match the research. **The one large measurable gap is word density: 2.3-2.8 words/s finished against 4.8-5.0 in the reference Shorts and 3.5-5.5 in the earlier transcript sample.** That is item 1.

---

## 1. Ranked plan

Types: script / voice / look / sound / app-feature / analytics / policy. Effort S = hours, M = 1-3 days, L = a week or a new dependency. "Why" cites the finding; P = PanoHadel report, F = field report, T = trends report, SG = style guide, DB = direction brief, M = my measurement above, C = current code.

### Tier 1: before the next batch is produced

| # | What (concrete) | Why | Expected effect | Effort | Type |
|---|---|---|---|---|---|
| 1 | **Double spoken density.** Writer kit target: skit 90-130 words / 25-35 s finished (3.3-4.0 w/s); storytime 200-270 words / 50-60 s finished (4.3-4.8 w/s). Add a `rapid` Piper pace (length_scale ~0.78) as DB §1.3 specifies; add a validator **warning** when finished words/s < 3.0 (storytime) or < 2.8 (skit); cut inter-line gaps to 0.05-0.10 s story-time. Keep the ASR dialogue-vs-script QA as the intelligibility gate: if word-error rises, settle at ~4.2 w/s. | P2 (4.8-5.0 w/s, 281-348 words in 56-73 s); SG §4 (3.5-5.5 w/s); M (we run 2.3-2.8) | More jokes per second is the clearest shared trait of the reference and the transcript sample; expect higher average-percentage-viewed [HYP] | M | script + voice + app-feature |
| 2 | **Two length bands in prefs.** Raise `production.max_seconds` 60 → 90; add a `band` field per plan: `skit` (target 30, range 25-35) and `storytime` (target 55, range 50-75). Stop treating 28 s as the single target. | P2 (56 s, 73 s); DB [T] story breakouts 75-142 s; skits 16-36 s; C (`target_seconds` 28, max 60) | Storytime pieces stop being trimmed into the stalled "middle" length; lets the roast-ladder format breathe | S | app-feature (owner decision) |
| 3 | **Title scorer** in the editor and batch check (`blox/story`): rule-based score 0-10 for: 30-55 chars; starts with He/She/My/I/They or a name; past-tense conflict verb; trivial trigger noun; ≤1 ellipsis; 1-2 emoji matching the plan's emotional lane (💀 comedy/betrayal, 🥺 warm, 😱/🤯 shock); no banned wording ("99 %", "true story", "big mistake", "made everyone cry"); no trademark; truth-to-video (tf-idf overlap with `hook.text` + `payoff.text` ≥ threshold). Store the score as a learning feature. | P3 (consistent 5-8-word pronoun-villain grammar across 4 titles); F4 (emotional conflict + 1-2 emoji); SG §2 (21/44 outliers carry emoji) | Consistent packaging; gives the learning module a title feature to correlate with retention | S-M | app-feature + analytics |
| 4 | **Hook-at-zero QA gate.** Tighten `validate.py` hook rule from "hook beat within 3 s" to: first voiced word ≤ 0.3 s finished, a caption chunk rendered on frame 0, hook text in the top band for 0-2.5 s, conflict stated in sentence 1 (hook.text must contain the antagonist pronoun/name and the trigger). | F (15/15 top Roblox Shorts hook at 0 s; 12/15 captions at 0 s, SS); P1 (conflict in sentence 1) | Prevents regression as new writers/LLM paths come online; current plans already pass | S | app-feature (QA) |
| 5 | **Variety guard at batch level.** Extend the originality screen with a batch-pattern check: per 12 plans max 2 consecutive same setting, ≤ 40 % same ending type, lead/narrator rotation logged, premise-lane rotation (family, rule-world, public micro-conflict, competition, warm, choice, series), and a stored "pattern report" the owner can show if reviewed. | T5 (July 2026 "inauthentic content": "characters repeatedly placed in the same situation", judged at channel-pattern level; animation channels were swept Jan-Feb 2026); T1 (Oct 1 originality recommendation change) | Lowers the biggest policy risk of a high-volume formulaic channel while keeping the recurring cast | M | app-feature + policy |
| 6 | **Turn the watchlist on with real data.** Owner connects a YouTube Data API key; seed `channel.reference_urls` / watchlist with: @PanoHadel, Sillycat Blox, Piee Animation, narrow (@nnarow), Hawks RBX, Ninye, Zevireth, PMdamiann, Davebb933, Mashles, Glince, Noobified, ItsGuestCap, BloxyRiddles, DevMint, RobloxArtSchool (all from the field/trends reports; the small ones are our true peers). Cost ≈ 1 unit per channel per discover run + 2 units per 60-video snapshot: trivial against the 10,000-unit day. | All three reports failed to fetch anything; C: `research/service.py` already does watchlist uploads, snapshots and measured velocity (≥2 snapshots) but has never run with a key | Replaces snippet guesses with measured velocity within 48 h; makes the top-10-vs-median and growth questions answerable | S (connect) | research (owner action) |
| 7 | **Halloween mini-season.** Four originals in `night_forest`/night `sky_obby` with `spooky_low` lighting, scheduled 17-31 Oct (premises 6 and 7 below plus two more), then a Nov 1-8 "clocks back / long night" pair. No pumpkin-brand or franchise costume; use `crown`, `beanie`, `bow`, campfire, lamp. | T7 (big in-game Halloween events 17 Oct; Halloween Sat 31 Oct; Nov 1-8 hooks) | Rides the only sure 4-week seasonal wave with zero IP risk | S | script |
| 8 | **Fix the narrator anchor.** Bloxy narrates ≥ 70 % of storytime pieces in one unchanging costume; Pip narrates the sibling pieces; Rook/Dot/Ms. Tally rotate as antagonist, judge, button-line character. | P4 [HYP] (same narrator silhouette 33 days apart); F3 (recurring characters convert virality to subscribers: Piee, Davebb933) | Brand recognition in the feed; A/B-able via the `lead_character` learning feature | S | script + look (owner decision) |

### Tier 2: this month

| # | What | Why | Expected effect | Effort | Type |
|---|---|---|---|---|---|
| 9 | **Series support.** Plan metadata gains `series {name, season, episode, recap_line}`; "Part N" only when `series` is set; Part 1 must pay off a mini-promise; Part 2 opens with a 2 s recap and posts within 24-48 h; publisher pins a comment linking the next part; add a "Shorts series" step (verify API support; may be manual in Studio; YPP-only per snippets). Pilot: the three-part Obby Cup (premise 1). | T2 (Shorts series launched 23 Sep 2026, SS); F3 (serialisation); SG T5 (≤ 1 in 4 cliffhangers) | Converts a breakout into sequential views and subscribers | M | app-feature (owner: YPP status) |
| 10 | **Reliable word timing for the yellow active word.** Add a local forced aligner (or local ASR word timestamps) so the caption highlight does not drift at 4.5 w/s; QA tolerance ±120 ms per word. | P5 (yellow active-word captions, REF B); DB §1.3 (Piper gives no word timestamps; drift risk at speed); C (task 22 built the highlight on estimated timing) | Captions stay credible at the new density; needed before item 1 is pushed to 4.5+ w/s | M-L | look + app-feature (owner: new dependency) |
| 11 | **Look additions:** `tint` effect (red anger flash, 6-12 frames), `bounce` (screen bounce on a stinger), idle head-bob, a `stomp_off` walk-off action, and our own entrance device (door_chime + name-tag card in the top band). | P5/DB §3.2-3.4 (freeze, red tint, head-bob, stomp walk-off observed; freeze/zoom_punch/shake/whoosh already built in `schema.py`) | Completes the "fast comedy" device set without copying any specific gag | M | look |
| 12 | **Sound polish:** confirm the `playful` bed sits at 130-150 BPM; add 2-3 stinger variants; cap `laugh_burst` at 2 per video and `fail_horn` at 1 per 5 videos; keep `whoosh_on_cuts` on for skits, off for the sad beats; keep the 0.6 s dropout. | P5 (SFX density rose to a whoosh on almost every cut, laugh track, stingers); SG §5; M (22-44 SFX/min already) | Matches reference density without a canned feel [HYP] | S | sound |
| 13 | **Covers as an A/B arm.** Set `publishing.upload_thumbnail` true for half of each batch (custom Shorts thumbnails reportedly available since July 2026, SS: verify). Cover rule: one face at `screaming`/`frozen`/`smug_max`, 2-3 word text that is not the title, `cover_t` at the reveal reaction (plans already set it). | P5 (thumbnails could not be inspected: open question); T2 (series carry custom thumbnails); C (`assembly.thumbnails` exists, upload off) | Tests whether covers matter for our feed-driven views; required anyway for series | S | look + analytics (owner decision) |
| 14 | **Metadata A/B + wording.** Description variant A: title only (reference style). Variant B: comment question + "Original characters, hand-animated in Blender" + 3-5 hashtags. Put the hand-made line first in both footers; keep the honest AI-assist disclosure but after it. Decide the `roblox` tag/footer wording (owner). | P3 (title-only descriptions, 4-5 generic tags); F7 (hand-made/original is now a positioning advantage; AI-slop crackdown); SG §2; C (`publishing.tags` includes `roblox`) | Data on whether descriptions move anything; lower misleading-metadata and slop-classification risk | S | analytics + policy |
| 15 | **Learning features.** Extend `learning.FEATURES` with `title_score`, `hook_formula`, `words_per_sec`, `narration_share`, `band`, `series`, `cover_variant`, `description_variant`, `cuts_per_10s` (numeric); make `engagedViews` the primary count and add first-3-s and 30 % `audienceWatchRatio` points from the retention report already requested in `youtube/analytics.py`. | T4 (public views now count from first frame; engaged views are the stricter count); C (analytics client already asks for engagedViews and audienceWatchRatio) | Turns every A/B above into a finding-or-hypothesis with a bootstrap CI | M | analytics |
| 16 | **Per-video hook A/B (rotation, not duplicates).** Each plan carries `hook_variants: [A, B]` (two first-sentence + hook-text pairs for the same story); the producer assigns variants round-robin; the learning module compares `hook_formula` across videos. Never upload the same story twice. | SG §1 hook formulas; T1 (re-uploads/template variants lose reach) | Hook learning without the duplicate-content penalty | M | analytics + app-feature |
| 17 | **Research-notes import (R6).** A paste/upload box in Trend research for Claude chat exports, extension CSV/JSON and URL lists: stored as untrusted notes with provenance, titles/numbers extracted, surfaced as "owner-supplied" references in the brief. | Owner request (chats, extensions, connected channels) that this session could not reach | Lets the owner's own gathered data feed briefs next run, with the same injection screening as `untrusted.py` | S-M | research |
| 18 | **TikTok export.** One-click 9:16 export with a per-platform caption and hashtag set (`#animation #funny #stories` + topic tag; `#roblox` per the owner's tag decision); manual upload first, API later. | P6 (same-day cross-posting of the same title); F (Hawks, Piee, PMdamiann, GH'S all run a TikTok surface) | Second discovery surface at near-zero production cost | S (export) / M (API) | app-feature (owner: account) |
| 19 | **Trend-backdrop field.** Brief gets `trend_backdrop` from a generic-label list (egg-hatching pet-raid game, woods-survival camping game, mascot-horror toy world, base-raiding, weekly "Update N" culture, the "pays you to stare at a screen" satire); 1 in 4 stories rides one; validator rejects trademarked names in title/tags. | F2 (winners ride the live game wave with an old legible archetype); T1b (Steal-an-Egg, 99 Nights, mascot horror); T10 (treadmill satire) | Topicality without IP risk | S | script + policy |
| 20 | **Choice endings and pinned questions** kept at 2 per 12, only if comments are on; otherwise swap for callback endings. | SG §3 T3 (choice formats 1.8-3.1 % comment rate vs ≤ 0.06 %); P7 (0.02 % comment rate on reference comedy) | Comment signal where it is cheap; no wasted bait | S | script (owner: audience setting) |

### Tier 3: after the first measured data

| # | What | Why | Expected effect | Effort | Type |
|---|---|---|---|---|---|
| 21 | **Cadence ramp.** Start at 2-4 finished videos/day (storytime-heavy), not 12, until learning has ≥ 20 videos per arm; scale when the hit rate (videos > 3x channel median at 48 h) is measured. `schedule.max_per_rolling_24h` 12 → 4 for the ramp. | F5 (volume-of-generic is where stalled channels sit; winners are either 30/mo or 2-3/mo high quality); F8 (5-25K views realistic for a new channel); T3 (10M Shorts views per 90 d to keep Shorts revenue share from Feb 2027); T5 (repetition risk) | Fewer, denser videos; more learning per video | S | policy (owner decision) |
| 22 | **Voice bake-off.** Piper distinct-cast (pitch shift now implemented) vs one-voice mode, 6 videos each, judged by retention; Kokoro-82M only after the DB §1.5 licence checklist passes. | P7 (one-voice vs performed range unresolved; emotion carried by tags, faces, SFX either way); DB §1 | Picks the cheaper mode if it loses nothing | M | voice |
| 23 | **Breakout alerting and competitor velocity views** (spec in §11): subscriber snapshots per watched channel, 30-day delta, "video > 10x channel median at 48 h" alert, per-channel top-10-vs-median table, title format classifier. | P (growth curve and top-10-vs-median could not be computed without a video list); F (field map is snippet-only) | The app answers the "who is popping off" question itself every 6 h | M | research |

---

## 2. Story formats and premises that are working now

**Formats with evidence (all SS/REF; ranked by how often they appear across the three reports):**
1. **First-person storytime hybrid**: past-tense narrator who is also the protagonist, quoted lines with speech tags carrying emotion, one set, 5-7 escalations, a neutral authority who sides against the villain, a surprise last line from a side character, hard cut, no CTA (P1; 3.09M and 170K-in-a-day examples).
2. **Public micro-conflict / entitled person humbled** by a rule or a neutral judge (P, REF B pattern), and the **roast/comeback ladder** (4-5 status claims, each answered). Our comebacks must target claims and actions, never looks or family situation.
3. **Rule-world** ("In this town/house/class…") mystery or comedy with a twist (trends_now rows 13-15: 1.2-1.4M views/day).
4. **Sibling/family skits** 16-36 s (Mashles 33M, Hawks RBX 8-13.5M).
5. **Betrayal-turned-warm / "then I saw why"** curiosity twists (Glince 42x outlier, DevMint).
6. **Archetype-vs-archetype on the newest game** (Sillycat "Pro vs Rich", +512K subs/30 d): we ride only the generic mechanic.
7. **Recurring characters whose Shorts accumulate into a larger story** (Piee: 63M-view Short, low upload frequency) and **serialised parts** (Hawks).
8. **Choice / "which one is correct"** loops (highest comment rates; 5-s quiz loops in the trend feed).
9. **Nostalgia / warm** (GH'S "I miss when we played together", 1.6M/day).

**Not working / avoid:** TTS over generic footage with no characters (2.7K-14K avg views at 80K-836K subs); derivative IP re-animations (plateaued, and the target of the Oct 1 change); long-form drama cut into Shorts (116K avg at 5.2M subs); real-creator skits; romance; audio-dependent memes (every named audio trend this week is copyrighted).

**12 fresh original premises** (none reuses a reference plot, the brief's 12, or the six writerA stories; no food escalation, emergency services, weapons, pets-and-seats, animal judges, appearance roasts, brands or real people):

| # | Premise (one line) | Cast | Lane | Band / setting | Title sketch (original) |
|---|---|---|---|---|---|
| 1 | Ms. Tally announces a five-round Obby Cup; Rook arrives with a trophy already engraved with his name, and Round 1 turns out to be "help one person", which he cannot do because he has been rude to everyone all week. | Bloxy (narr.), Rook, Dot, Pip, Ms. Tally | Karma + **series pilot (3 parts)** | Storytime 55 s, sky_obby | "He Engraved the Trophy Before the Race… 💀" |
| 2 | In Ms. Tally's class every "easy" slides your desk one block toward the lava block by the door; Rook's desk is already on the edge and Dot keeps asking him innocent questions. | Rook, Dot, Ms. Tally, Bloxy | Rule-world comedy, villain's own words backfire | Skit 30 s, classroom | "Every 'Easy' Moved His Desk Toward the Lava 😳" |
| 3 | Rook opens a "game" that pays one coin per minute to watch a sign spin; Bloxy earns four coins and loses the whole afternoon, while Dot earns a hundred selling juice to the watchers and then switches the sign off. | Bloxy (narr.), Rook, Dot, Pip | Satire of the pay-to-stare loop, status flip | Storytime 50 s, town_street | "He Paid Us to Stare at a Sign… 💀" |
| 4 | Rook finds Dot's lost key and demands a reward before handing it over; Ms. Tally reads the lost-and-found rule (finders get a thank-you card), and Dot's card says "thanks for holding it, the door changed locks yesterday". | Dot, Rook, Ms. Tally, Bloxy (narr.) | Entitled person vs neutral rule | Storytime 45 s, classroom | "He Wanted a Reward for MY Key… 😏" |
| 5 | Rook pays Pip ten coins to be his personal hype man for the lava race; Pip takes the job so seriously he hypes everyone on the start line, and Dot, who entered by accident, wins. | Rook, Pip, Dot, Bloxy | Competition comedy, warm | Skit 35 s, lava_obby | "He Hired My Best Friend to Hype Him… 😂" |
| 6 | On the campfire's Halloween night your costume becomes real for sixty seconds; Rook, dressed as a king, spends his minute ordering ghost-Dot to carry his candy bucket, and the minute runs out mid-decree while hers has not started. | Rook, Dot, Pip (narr.), Bloxy | Seasonal karma comedy | Storytime 50 s, night_forest, spooky_low | "His Costume Made Him King for 60 Seconds 👑💀" |
| 7 | On the Halloween night obby every checkpoint flag whispers your last fib back at you; Rook has told so many that the flags will not stop talking and he cannot hear the countdown. | Rook, Bloxy (narr.), Dot, Ms. Tally | Rule-world spooky comedy | Skit 35 s, sky_obby at night | "The Checkpoints Repeat Your Lies… 😱" |
| 8 | Bloxy catches Pip sneaking off to train with Rook every evening; the "betrayal" is Pip learning Rook's jump so he can teach it to Dot before her first race. | Bloxy (narr.), Pip, Rook, Dot | Betrayal turned warm | Storytime 50 s, lava_obby | "I Caught My Best Friend Training With My Rival 🥺" |
| 9 | Ms. Tally lets Bloxy pick one obby partner: Rook (fastest, quits when it gets hard), Pip (slowest, never quits) or Dot (tiny, builds a bridge instead of jumping); the choice goes to the viewer. | Ms. Tally, Bloxy, Rook, Pip, Dot | Choice / comment | 15-20 s, studio | "Pick Your Obby Partner… Choose Wisely 👇" |
| 10 | Ms. Tally's rule: the best excuse for missing homework earns a pass; Rook says a dragon ate his, Bloxy admits he did Rook's instead of his own, and Dot just hands hers in and gets the pass "for not needing one". | Rook, Bloxy, Dot, Ms. Tally | School comedy, fair judge | Skit 30 s, classroom | "A Dragon Ate His Homework… Sure 😂" |
| 11 | Dot makes Bloxy a "best friend" badge out of a rock and Rook laughs at it; the final door of the sky obby only opens for something that was given, not bought, and Rook's diamond badge does nothing. | Bloxy (narr.), Dot, Rook, Pip | Status flip, warm | Storytime 50 s, sky_obby | "He Laughed at Her Rock Badge… Then the Door 😳" |
| 12 | Dot sold tickets to watch Pip fail the lava obby; Pip finds out and clears it first try, so Dot refunds everyone except Rook, who paid double for a front-row seat. | Pip (narr.), Dot, Rook, Bloxy | Sibling comedy + karma | Skit 30 s, lava_obby | "She Sold Tickets to Watch Me Fail… 💀" |

Ending mix across the 12: karma/comic 5 (1, 2, 4, 6, 12), warm 3 (5, 8, 11), double twist/callback 2 (3, 7), choice 1 (9), series cliffhanger 1 (1 → Part 2). Run all through the originality screen; `inspiration.note` should cite "pattern-level research only".

---

## 3. Hook and title patterns

- **Spoken hook:** the whole premise in sentence 1, ≤ 7 words at the new pace, first word by 0.3 s, no establishing wide, open on a close-up already in motion (P1, SG §1, F1). Hook text on screen 0-2.5 s, ≤ 6 words, top band.
- **Observed title grammar (P3, REF + SS):** `[He|She|My + role] + past-tense extreme verb + [my|a] + trivial trigger + optional "…" + 💀/😳/🥺/😱`, 5-8 words, title case, villain by pronoun, no spoiler, no numbers, no "POV"/"Part" unless a series. Our six current titles already follow it; item 3 makes it a score.
- **Proven hook shapes to rotate (item 16):** huge reaction to a tiny cause; absurd entitled demand in a public place; impossible rule; "then I saw why" curiosity gap; status flip ("they laughed at my…"); choice. Keep SG's 10 formulas as the variant pool.
- **Description:** A/B title-only vs question + disclosure + hashtags (item 14). No spoken CTA in the video (both references have none).

## 4. Pacing numbers

| Metric | Reference (P, n=2) | Wider sample | Ours now (finished) | Target |
|---|---|---|---|---|
| Words/s | 4.8-5.0 | 3.5-5.5 (SG transcripts) | 2.3-2.8 | Skit 3.3-4.0; storytime 4.3-4.8 (floor 4.2 if words smear) |
| Words per video | 281 / 348 | 96-387 | 68-151 | Skit 90-130; storytime 200-270 |
| Narration share | 40-60 % (rising dialogue share) | — | 0-53 % | Storytime 40-60 %; skit 0-20 % |
| First spoken word | sentence 1 = conflict | 15/15 hook at 0 s | 0.13 s | ≤ 0.3 s, caption on frame 0 |
| Joke interval | 3-8 s | — | not measured | ≤ 5 s skit, ≤ 6 s storytime |
| Escalation beats | 5-7, new character/claim every 8-12 s | — | ~4-6 | 5-7 storytime, 3-4 skit |
| Average shot | ~2.1 s; ~1 s in fast runs | "something changes every ~2 s" (SS) | 1.36-1.75 s | 1.3-2.0 avg; 0.8-1.0 in runs; reveal hold 2-2.5 s only |
| SFX | medium → dense (whoosh on most cuts, stingers, laugh track) | 10-16 per 25-30 s (SG) | 19-44 per min | 20-30 per min + `whoosh_on_cuts`; laugh_burst ≤ 2 |
| Music | bed + cue at twist | 3-4 cues (SG) | 4-5 cues | 3-5 cues + 0.6 s dropout before the punchline |
| Length | 56 s, 73 s | story breakouts 75-142 s; skits 16-36 s | 29-53 s | Skit 25-35 s; storytime 50-75 s |
| Engagement | like 1.3-2.6 %, comment 0.02 % | choice formats 1.8-3.1 % comments | — | Track engaged views and 3-s retention, not comments |

## 5. Voice direction

Keep Piper (free, offline). The references carry emotion through speech tags, face swaps and SFX, not acting, which Piper can match (P7, DB §1.4). Per role (DB §1.2 settings as the starting point): narrator mid-pitch, crisp, slightly smug, 4.5-4.8 w/s, one 0.4-0.6 s pause before each comeback; rival brighter/+1-2 st with a drawl on brags; Dot +3-5 st, 1-6-word bursts, gets the button line; Ms. Tally low, −1-2 st, 1-3 verdict lines; Pip dry, half a beat late. Add `rapid` pace; +3 dB on `shout`. A/B one-voice vs distinct cast (item 22). Never audition a voice because it resembles a creator; never feed reference audio to any engine.

## 6. Visual and edit effects

Already built (`schema.py`): `zoom_punch`, `shake`, `freeze`, `expression_snap: cut`, `whoosh_on_cuts`, `music_dropout`, big-reaction presets (`screaming`, `smug_max`, `mischief`, `frozen`), costume vocabulary (bun, pigtails, spiky, crown, glasses, jacket, vest, cardigan, tie), `door_chime`, `crowd_ooh`, `laugh_burst`. To add (item 11): red `tint`, `bounce`, idle head-bob, stomp walk-off, name-tag entrance. Grammar: cut on every speaker change; low angle on whoever is winning, swapped at the turn; inserts 0.6-1.0 s on the key prop; 30-40 % of lines over the listener's reaction; one set per video; new sets in priority order: home interior (hallway/living room), then public counters (shop, cafeteria, park).

## 7. Thumbnails and covers

Nothing was observed about the reference's covers (thumbnails blocked). Treat covers as an A/B (item 13): one face, extreme expression, 2-3-word text that does not repeat the title, at the reveal reaction; required for series. Keep `upload_thumbnail` off for the control arm.

## 8. Posting cadence and length

Evidence is bimodal (F5): ~30/month of trend-riding re-animation, or 2-3/month of high-effort originals; the "3/week of generic" middle stalls. For an original-character channel at launch: 2-4/day of dense storytime + skit mixes, measured, then scale (item 21). Length: two bands (item 2). Same-day cross-post to TikTok (item 18). Part 2 within 24-48 h of a Part 1 (item 9).

## 9. Metadata and hashtags

- Tags: 4-6 generic (`animation`, `story`, `funny`, `storytime`, `blocky`, topic tag). **Owner decides `roblox`** (trademark/misleading-metadata risk for original Blender characters; both references and the whole niche use it, so it is a reach-vs-risk trade).
- Description: A/B per item 14; the "hand-animated in Blender, original characters" line first; honest AI-assist line after it.
- Hashtags: `#shorts #animation #story` + 1 topic tag; TikTok `#animation #funny #stories` + topic.
- Category 20 (Gaming) stays unless analytics says otherwise.

## 10. Policy risks (ranked)

1. **Inauthentic/repetitive content at volume** (T5): mitigated by item 5 and the cadence ramp; keep the pattern report.
2. **Originality ranking change, 1 Oct 2026** (T1): never re-upload variants; hook A/B by rotation only (item 16).
3. **YPP economics** (T3): 10M Shorts views per rolling 90 d to keep Shorts revenue share; new terms by 31 Jan 2027. Owner decision on timing; do not plan revenue before it.
4. **Audience setting** (SG §2): `made_for_kids` is `None` in prefs; it decides comments, hence choice endings. Owner must set it deliberately (13+ general audience is the current niche note).
5. **Trademarks and IP:** no game names, logos, other channels' characters, franchise names in content, titles, tags, thumbnails (DB §5). `roblox` tag/footer: owner decision.
6. **Violence/brands in the reference itself** (REF A has a gun and a real brand): never replicate; harmless props only.
7. **AI labels** (T6): animated content reported exempt unless photorealistic; `synthetic_disclosure` is `None`: owner confirms the current rule and sets it.
8. **Audio rights:** every trending audio this week is copyrighted; studio-generated music/SFX only.
9. **Mean humour:** no roasts on looks, body, family or follower counts; no "Karen"-type labels.
10. **Minor safety:** no dares that copy real-world danger, no account/password/real-money content.

## 11. Research and analytics features the app should have (spec)

Existing: Data API client, quota ledger, watchlist uploads, snapshots, measured velocity (≥ 2 snapshots), explainable ranking with `channel_relative`, emerging topics, originality screen, learning with bootstrap CIs, retention report. Add:

1. **Competitor watchlist with velocity tracking:** per watched channel store subscriber/view snapshots each discover run; show 7/30-day subscriber delta and uploads/30 d; rank channels by 30-day growth (the "popping off" view). Cost ~1 unit/channel/run.
2. **Breakout alerts:** flag any watched video whose views at 24/48 h exceed 10x that channel's median of its last 20 Shorts; push to the dashboard and to the brief as "pattern only".
3. **Top-10-vs-median per channel:** median views of last 20 Shorts, top 10 list, length band, title grammar, posting gaps (answers the PanoHadel questions nobody could fetch).
4. **Format classifier on titles/descriptions:** storytime, skit, rule-world, choice, series, POV, comparison; stored as a feature; used by `emerging_topics`.
5. **Title scorer** (item 3) and **hook-variant rotation** (item 16), both feeding `learning.FEATURES` (item 15).
6. **Notes import** (item 17) for Claude chats, extension exports, URL lists; untrusted, provenance-tagged.
7. **Own-channel learning:** engaged views, average % viewed, 3-s and 30 % retention per video, compared by band, hook formula, title score, narrator, cover and description variant; findings vs hypotheses as today.
8. **Pattern report** for policy (item 5).
9. **TikTok mirror export** (item 18).
10. **Quota guard already exists;** keep `reserve_shared_units` 1500 for publishing.

## 12. Decisions the owner must make

1. Provide a YouTube Data API key and (ideally) the analytics scope on the connected channel; authorise the ThoughtLeaders YouTube connector in claude.ai connector settings if it is to be used; decide whether to top up TubeAlfred (1 credit answers the PanoHadel Shorts-list question) — vidIQ stays off unless re-authorised.
2. Export the two Claude chats ("Viral Roblox brain rot video", "Minecraft economics video series") and the Chrome extension data as text/CSV for the import box (item 17); nothing from them was used here.
3. Confirm the creator meant by "hadelfilms" is `@PanoHadel`.
4. `made_for_kids` setting (comments on/off → choice endings).
5. `roblox` in tags/title/footer.
6. Cadence: 2-4/day ramp vs the 12/day design target.
7. Length bands: raise `max_seconds` to 90.
8. Shorts series: YPP status, accept new terms by 31 Jan 2027, and whether Part 1/2 episodes are wanted.
9. New dependency for word timing (forced aligner or local ASR).
10. Custom cover upload A/B on.
11. TikTok account and same-day cross-posting.
12. Fixed narrator (Bloxy) vs rotation.
13. `synthetic_disclosure` and the footer's AI-assist wording.
14. Voice engine bake-off and the Kokoro licence check before any switch.