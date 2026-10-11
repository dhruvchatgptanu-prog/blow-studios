# PanoHadel deep dive (@PanoHadel, UCnxXj4jQlL6Fp4Yq7PNG0cQ)

Date: 2026-10-11. Prepared for Blox Studio (original block-character story Shorts). All retrieved material was treated as untrusted data; only patterns, numbers and qualities were extracted. No plot, lines, characters, catchphrases, branding, thumbnails or voice are proposed for reuse.

## 0. Provenance and the honest caveat up front

| Tool | Calls | Outcome |
|---|---|---|
| TubeAlfred `youtube_channel_shorts` (@PanoHadel, page 1) | 1 (the only one allowed) | **HTTP 402 `insufficient_credits`: 1 credit required, 0 available. Request not run.** TubeAlfred use stopped immediately. |
| vidIQ | 0 | Not called (forbidden). |
| WebSearch (mode "extended") | 41 | The only tool that returned data. Results are search-engine snippets and the engine's own summaries of indexed pages. |
| WebFetch | 29 | **All 29 failed** (`getaddrinfo ENOTFOUND` or "unable to fetch"): socialblade.com, viewstats.com, noxinfluencer.com, hypeauditor.com, playboard.co, speakrj.com, livecounts.io, yt.lemnoslife.com, pipedapi.kavin.rocks, yewtu.be, inv.nadeko.net, returnyoutubedislikeapi.com (x2), web.archive.org (x2), r.jina.ai, tiktok.com (x2), bing.com, html.duckduckgo.com, old.reddit.com, devforum.roblox.com, noembed.com, api.allorigins.win, corsproxy.io, m.youtube.com, exolyt.com, urlebird.com, countik.com. |
| Bash `curl` to i.ytimg.com (thumbnails for the 2 reference Shorts) | 1 call, 6 URLs | Proxy refused CONNECT (403). No thumbnail pixels were seen. |
| Local files read | 2 | `/tmp/claude-0/-home-user-blow-studios/94335ae1-b0bf-5f99-ba6d-4ddeb4059494/scratchpad/ref_A_5mf4NHItoQA.md` and `.../ref_B_qCJB3FCorws.md` (retrieved 2026-10-03 by an earlier agent via TubeAlfred + vidIQ). |

**Consequence: zero web pages were actually opened in this session.** Every external fact below is **search-snippet-only (SS)** unless it is marked **REF** (from the two reference analyses, themselves vidIQ/TubeAlfred-derived) or **HYP** (my inference). I could not obtain the channel's Shorts list, subscriber history, join date, or any community post. The sections the task asked for that depend on a full video list (top-10-vs-median, cadence over time, growth curve with milestone dates) are therefore partial and labelled as such.

## 1. Identity and footprint

- **YouTube:** channel ID `UCnxXj4jQlL6Fp4Yq7PNG0cQ`, indexed title "PanoHadel - YouTube", handle `@PanoHadel`, description "I make roblox animations." (SS, from the search engine's structured-data readout of the channel page.)
- **Unlabelled count 45,600:** the same channel page's `interactionStatistic` field reads 45,600 (SS). The engine's summary could not say whether this is subscribers, and the crawl is undated. **HYP:** it is a stale subscriber snapshot from before the August 2026 breakout; a channel with a 3M-view Short in its last 45 days is very likely larger now. Do not quote 45.6K as current.
- **Possible earlier identity "JustAira":** three separate searches returned the same channel URL with the indexed title "JustAira" (SS). No "JustAira" Roblox or YouTube presence exists elsewhere in the index. **HYP:** the channel was renamed/rebranded to PanoHadel (the index holds both titles for one URL). This matters for the growth story: the channel probably pre-dates the story-Shorts format.
- **TikTok:** `@panohadel_`, display name "PanoHadel" (SS, multiple TikTok "discover" aggregator pages). Same titles, same hashtag set `#roblox #animation #funny #stories`.
- **Fan/compilation ecosystem forming:** a different creator's TikTok promotes "Best PanoHadel Stories to Watch While You Eat" (posted ~6 days before 2026-10-05) (SS). That is a classic breakout signal: third parties are now packaging the brand.
- **No Reddit, X, Discord, interview, DevForum or press mention** surfaced in 41 searches. The creator appears to publish only through the videos themselves. "hadelfilms" (the owner's name for the creator) returned nothing; the only "Hadel" in the index is Zach Hadel (psychicpebbles), unrelated.

## 2. Growth curve and milestones (what can and cannot be said)

Hard datapoints available:

| Date | Item | Numbers | Source |
|---|---|---|---|
| ~2025-03-31 (HYP) | TikTok "He Pretended My Lamborghini Was His..." | one listing labels it "559 days ago" (another says "6 days ago", so the age label is unreliable); ~347.6K views; 27.6K likes / 251 comments at a 2026-10-05 listing update | SS |
| 2026-08-30 | YT Short "He Called SWAT Over A Nugget💀" (5mf4NHItoQA), 56 s | 3,085,943 views / 40,003 likes / 635 comments at 34 days (2026-10-03); like rate 1.30%, comment rate 0.021% | REF A |
| 2026-10-02 | YT Short "She Wanted My Seat For Her Dog... 💀🐶" (qCJB3FCorws), 73 s | 169,512 views / 4,372 likes / 33 comments at ~1 day; like rate 2.6% | REF B |
| ~2026-10-02 | Same title on TikTok | 140 likes / 28 comments (an early snapshot on an aggregator page) | SS |
| ~2026-10-04/05 | TikTok "She Fought Me Over GTA 6 💀" | 685 likes / 10 comments on one listing (updated 2026-10-05), ~9,430 likes / 374 comments on another ("six days old"); creator's own comment: "I might post abt this game :3" | SS |
| undated | Other TikTok posts by the account | view counts quoted at 12K, 49.1K, 242.3K, 375K, 469K | SS |

What this supports:
- **HYP (medium confidence):** the story format started on TikTok around spring 2025 (if the 559-day label is right), ran at tens-to-hundreds of thousands of views per post, and the YouTube breakout came in late August 2026 with the 3M-view Short. The early-velocity numbers on the Oct 2 Short (170K in ~1 day, 2.6% like rate) are consistent with a channel whose Shorts now get pushed hard by the feed.
- **Not obtainable here:** subscriber milestones with dates, total channel views, join date, upload count. These need one of: a TubeAlfred credit top-up (1 credit = `youtube_channel_shorts` page 1), or, better, the owner's own YouTube Data API key through the app's existing research service (`channels.list` + the uploads playlist costs ~3 quota units and returns every Short with date and view count, which is what the top-10-vs-median analysis needs).

## 3. Posting cadence and length band

- **Cadence (partial):** known public dates: Aug 30 (YT), Oct 2 (YT + TikTok), ~Oct 4-5 (TikTok). Two posts within ~3 days in early October (SS). **HYP:** at least 2 per week on TikTok in early October 2026; YouTube cadence unknown. Same-day cross-posting of the same title to both platforms is observed once (REF B + SS).
- **Length band:** 56 s and 73 s (REF A, REF B). Both sit inside the 35-58 s / 60-90 s bands that third-party Shorts guides report as the retention sweet spot for narrative niches (SS, vendor data, unverified). **HYP:** the later video being longer (73 s) with a denser joke count suggests the creator is comfortable stretching toward 70-90 s when the roast ladder justifies it.

## 4. Format and its evolution (storytime narration, dialogue, POV)

Both reference videos are the same hybrid: a first-person past-tense narrator ("I was sitting on the bus...") who is also the protagonist, with the same single voice reading every quoted line, and speech tags carrying the emotion ("she snapped", "the officer sighed", "she froze") (REF A, REF B). It is not a POV-text format and not pure dialogue.

Observed drift between Aug 30 and Oct 2 (n=2, so treat as HYP):
- Script size grew (281 words -> 348 words); pace stayed at ~4.8-5.0 words/s.
- Dialogue share rose (40% -> 60% of words); the Oct 2 video is a roast ladder where the antagonist's status claims each get a one-liner back, versus the Aug 30 "new character enters, scene resets" escalation.
- Bystander laugh cues appeared (driver laughing, "choked on his coffee", a laugh-track SFX) in the later video; absent in the earlier one.
- A "neutral judge" twist (an animal/third party picks the hero) and a callback payoff appear in the later video; the earlier one ends on a mood-flip on the same object.
- Topicality entered the mix: "She Fought Me Over GTA 6" (SS) hooks a story to a game with a 2026-11-19 release date (SS, ladbible). That is new relative to the evergreen family/public-space premises.

Shared skeleton (both REF): conflict stated in sentence 1 (0-3 s); a joke every 3-8 s; one location; 5-7 escalation beats; a surprise last line from a side character; hard cut; **no spoken CTA**; no intro, no outro.

## 5. Title, description, hashtag and thumbnail patterns

- **Title grammar (consistent across 4 known titles, REF + SS):** `[He|She] + [past-tense extreme verb] + [My|A] + [trivial object or trigger] + [optional "..."] + 💀 + [optional one subject emoji]`. 5-8 words, title case, villain named only by pronoun, no spoiler, no clickbait numbers, no "POV", no "Part 1". The ellipsis is used as a cliffhanger on the longer ones.
- **Description:** the title repeated verbatim, no hashtags (REF A, REF B).
- **Tags:** 4-5 generic ones (animation, roblox, funny, story; plus "USA" once) (REF). On TikTok the caption carries `#roblox #animation #funny #stories` (SS).
- **Thumbnails:** could not be inspected (i.ytimg.com blocked; vidIQ off limits). Note that custom thumbnails for Shorts became available in July 2026 (SS, outlierkit), so whether the creator now uses a designed cover is an open question worth one credit when the owner has one.

## 6. Recurring characters and world

- **Recurring protagonist avatar (REF A vs REF B, per vidIQ descriptions):** both videos' narrator is a bald block avatar in a printed/graphic tee with an open jacket or overshirt. Same silhouette twice in videos 33 days apart -> **HYP:** a fixed, persistent narrator design is the brand anchor, while antagonists and supporting cast rotate per episode (brother/SWAT officer/dad/mom; entitled woman/driver/kid).
- **Worlds:** domestic (house foyer, stairs) and public transit (bus). Each episode is one set. Props carry the premise (a food item, a handgun, a dog, a car in the TikTok title).
- **Faces:** emotion is shown by swapping exaggerated meme-style face decals (smug, scream, manic, bored, troll grin, shocked) and an occasional red tint/"demon" overlay on anger (REF). Lip-sync was not reported.
- **Costume identification:** one strong silhouette item per character (helmet, horns, glasses, bowl cut, safety vest) (REF).

## 7. Voice approach (qualities only)

- Single young-adult male voice for all characters, mid pitch, high energy, ~300 wpm, near-zero inter-sentence silence, General American (REF A, REF B).
- The two vidIQ reads disagree on mechanism: REF A says "one synthetic TTS voice" with only "flat pitch shifts between characters"; REF B describes a performed-sounding range (high nasal whiny antagonist, low deadpan driver, pitched-up kid). **Unresolved.** No public statement by the creator about TTS vs performance was found (SS: nothing in 41 searches). **HYP:** either an AI voice with per-character pitch/speed variants or one performer doing light character voices; emotion is mostly carried by script tags, face swaps and SFX, not vocal acting, which is exactly what a Piper pipeline can match.
- Effects: at most a pitch-up on the child character; no robot/echo/reverb (REF).

## 8. Animation tooling

- **Not publicly stated anywhere found** (SS: searches combining the handle with Moon Animator, Blender, Roblox Studio, "how I make", "my setup", "behind the scenes" returned nothing about this creator).
- **HYP:** the look (R6/R15 proportions, decal face swaps, head-bob idle, snap zooms, whip pans, freeze frames) is consistent with either Roblox Studio + Moon Animator or Blender with Roblox-style rigs plus an NLE pass for zooms/whooshes. Blox Studio's Blender renderer can reproduce every listed visual device without knowing the creator's actual tool.

## 9. Music and SFX habits

- Aug 30 video: minimal quirky/tense bed, auto-caption `[music]` markers at 4 key beats (character entrances, the twist, the mood flip), medium SFX density (gunshot, gasps, footsteps, eating) (REF A).
- Oct 2 video: upbeat fast playful bed throughout, a music cue at the twist, **dense** SFX: a whoosh on almost every cut, a cartoon laugh track, animal bark/growl, punchy stingers on punchlines (REF B).
- **HYP (n=2):** production polish increased in the last 60 days: denser SFX, faster cut rate (~2.1 s/shot -> ~1 s/shot in fast stretches), and captions upgraded from plain white to white with a **yellow active word**.

## 10. Top Shorts vs median

Cannot be computed without the Shorts list (TubeAlfred 0 credits; all mirrors blocked). What the available points say:
- The one confirmed mega-hit (3.09M at 34 days) and the strong early-velocity follow-up (170K at ~1 day) share: pronoun-villain title with a trivial trigger and 💀; conflict in sentence 1; one set; a ladder of 5-7 escalations; an authority figure who sides against the villain; a button line from an unexpected character; no CTA.
- TikTok-side view counts quoted for the account range from ~12K to ~469K (SS), i.e. a heavy-tailed distribution where most posts land in the tens of thousands and a few break out. **HYP:** on YouTube the spread is similar, with the SWAT video being an outlier of >10x the typical post.

## 11. Audience and comment style

- YouTube comment rate is tiny (0.02% of views on both REF videos); like rate 1.3-2.6%. Passive, feed-driven comedy viewing.
- TikTok engagement is more conversational: 374 comments against ~9.4K likes (~4%) on the GTA 6 post, 251 comments on the Lamborghini post (SS). The creator replies in-thread with a casual, youthful register ("I might post abt this game :3") (SS).
- **HYP:** audience skews teen/young-teen Roblox players in the US (the "USA" tag, General American voice, family/school/bus settings, GTA 6 as a reference point). No demographic data was obtainable (HypeAuditor/NoxInfluencer blocked). Comment text itself could not be sampled.

## 12. Interviews, community posts, process

None found. No Community tab content, Discord, Patreon, X/Twitter or interview surfaced in the index (SS). The creator's only visible "voice" outside the videos is TikTok comment replies.

## 13. What changed in the last 60 days (Aug 12 - Oct 11, 2026)

1. **Breakout on YouTube:** the Aug 30 Short passed 3M views within ~5 weeks (REF A). Prior to that the only indexed subscriber figure is 45.6K (SS, undated) -> **HYP:** the channel is in its hockey-stick phase right now.
2. **Format got denser and longer:** 56 s -> 73 s; 281 -> 348 words; laugh cues, whoosh-per-cut, yellow-word captions appeared (REF A vs REF B; HYP as a trend).
3. **Topical hooks added:** "She Fought Me Over GTA 6" (~Oct 4-5) ties a story to the Nov 19, 2026 release (SS). Previous premises were evergreen.
4. **Third-party compilations appeared** ("Best PanoHadel Stories...") around Oct 5 (SS): the brand is now being re-packaged by others.
5. **Platform context (SS, third-party blogs, verify against YouTube's own posts):** YouTube announced a Shorts ranking update favouring original uploads over re-uploads (news dated Oct 2026); Shorts can be grouped into series with seasons/episodes (from Sept 23, 2026); custom Shorts thumbnails (July 2026); dislike button removed from the Shorts player (June 25, 2026); Shorts monetization will require 10M qualified Shorts views per 90 days from Feb 1, 2027.

## 14. Comparison against the two reference analyses

| Dimension | Ref A (Aug 30, 56 s) | Ref B (Oct 2, 73 s) | Read-across |
|---|---|---|---|
| Premise type | Family overreaction, escalating character entrances | Public-space entitled stranger, roast ladder + karma | Two repeatable lanes: "household absurdity" and "entitled stranger gets humbled" |
| Pace | ~5.0 w/s, 1 sentence/s | ~4.8 w/s, deliberate pauses before comebacks | 4.8-5 w/s is the house tempo |
| Cut rate | ~2.1 s/shot | ~1 s/shot in fast stretches | Cut on every speaker change; faster is newer |
| Captions | White, heavy black outline, 1-3 words | Same plus yellow active word, 1-4 words, word-synced | Yellow active word is the newer style (Blox Studio task #22 already matches this) |
| SFX | Medium, stings at beats | Dense: whoosh per cut, laugh track, stingers | Density went up (task #23 covers whoosh/cues; add a laugh-cue slot) |
| Ending | Mood flip on the inciting object | Callback to the villain's own rule + side-character button | Both are "earned" endings with no CTA |
| Risk | Gun drawn on a parent, officer shot | Another game's IP on a shirt | Blox Studio: non-violent escalation, no third-party IP |

## 15. Implications for Blox Studio (patterns only, nothing copied)

1. **Title compiler:** enforce the grammar `[Pronoun villain] [past-tense extreme verb] [My/A] [trivial trigger] ... 💀 [+1 subject emoji]`, 5-8 words, no spoiler, no "POV"/"Part". Description = title; 4-5 generic tags; cross-post caption `#roblox #animation #funny #stories`-style (use Blox Studio's own tag set, not the creator's exact one).
2. **Two story lanes in the backlog generator:** (a) household absurdity with staged character entrances that reset the scene; (b) entitled-stranger-in-a-public-place with a roast ladder, an authority figure who sides with the hero, a neutral judge, a backfiring rule, a side-character button.
3. **Tempo targets for the Piper pipeline:** 4.8-5.0 words/s, inter-line gaps ~0.1 s, 2-3 deliberate 0.4-0.6 s pauses before comebacks, gain boost on shouted lines, per-character pitch offsets of a few semitones (already in task #26).
4. **Length band:** 55-75 s default; allow up to ~90 s only when joke density stays >= 1 per 6-8 s.
5. **Fixed narrator avatar + rotating cast:** make one persistent protagonist design the brand anchor; give every other character exactly one silhouette item.
6. **Sound pass:** whoosh on nearly every cut, stinger on each punchline, music cue at the twist, add a laugh-track cue slot for bystander reactions.
7. **Topical hook field in the research service:** tag a minority of stories to live trends (a trending Roblox experience, a major game release, a seasonal event) without naming third-party IP in the visuals.
8. **Use the Shorts "series" feature** for the recurring narrator, and design a custom cover frame (protagonist reaction face + 3-word premise) now that Shorts thumbnails are supported.
9. **Same-day TikTok cross-post** of every Short: TikTok is where comments (and creator replies) happen; YouTube comments are near-zero for this format.
10. **Monetization KPI:** plan toward 10M qualified Shorts views per 90 days (the Feb 2027 threshold reported by third-party sources; verify with YouTube).
11. **Safety:** keep escalation non-violent (no weapons on family/police), avoid real brands and other games' IP in frame.

## 16. Open gaps and the cheapest way to close them

- **Shorts list with views/dates (needed for cadence, top-10 vs median, growth):** 1 TubeAlfred credit, or the owner's own YouTube Data API key via the app's research service (`channels.list` + uploads playlist, ~3 quota units). The sandbox cannot reach youtube.com or any mirror, so this must run from the owner's environment.
- **Subscriber history / join date / country:** Social Blade or ViewStats from the owner's browser (both blocked here).
- **Thumbnail/cover style:** fetch `i.ytimg.com/vi/<id>/oar2.jpg` for a handful of Shorts from the owner's machine and inspect.
- **Voice mechanism (TTS vs performed):** listen to one video; the two AI reads disagree.
- **Comment sampling for audience style:** 1 TubeAlfred `youtube_comments_list` credit per video once credits exist.

## Sources

Opened in this session: none (all 29 WebFetch attempts and the curl attempt were blocked). Local files read:
- /tmp/claude-0/-home-user-blow-studios/94335ae1-b0bf-5f99-ba6d-4ddeb4059494/scratchpad/ref_A_5mf4NHItoQA.md
- /tmp/claude-0/-home-user-blow-studios/94335ae1-b0bf-5f99-ba6d-4ddeb4059494/scratchpad/ref_B_qCJB3FCorws.md

Search-snippet-only URLs (surfaced by WebSearch; never opened):
- https://www.youtube.com/channel/UCnxXj4jQlL6Fp4Yq7PNG0cQ (indexed as "PanoHadel - YouTube" and, in other entries, "JustAira"; description "I make roblox animations."; interactionStatistic 45600)
- https://www.tiktok.com/discover/lamborghini-joke
- https://www.tiktok.com/discover/guy-pretends-to-own-lamborghini
- https://www.tiktok.com/discover/lamborghini-egoista-beam-ng
- https://www.tiktok.com/discover/understandable-have-a-nice-day-roblox
- https://www.tiktok.com/discover/fat-lady-putting-up-missing-dog-poster-dog-stuck-on-her-back
- https://www.tiktok.com/discover/best-things-to-watch-while-eating
- https://www.tiktok.com/discover/some-guy-was-flexing-on-my-lamborghini?lang=en (GIMZO, adjacent creator)
- https://www.tiktok.com/discover/bmw-fake-story-video (GIMZO, adjacent creator)
- https://www.ladbible.com/entertainment/gaming/gta-6-controversy-rockstar-tiktoker-720527-20260902 (GTA 6 release date context)
- https://outlierkit.com/resources/youtube-algorithm-updates/
- https://outlierkit.com/resources/youtube-shorts-algorithm/
- https://www.vanguardngr.com/2026/10/youtube-changes-shorts-algorithm-to-favour-original-videos/
- https://blitzcutai.com/blog/youtube-shorts-length-2026
- https://en.wikipedia.org/wiki/Steal_a_Brainrot
- https://studiokrew.com/blog/top-roblox-games-may-2026/
- https://www.pocketgamer.biz/robloxs-steal-a-brainrot-becomes-first-game-to-surpass-25m-concurrent-players/
- https://games.gg/news/start-your-blue-lock-career-codes (Oct 2026 list of popular Roblox experiences)
- https://en.wikipedia.org/wiki/Zach_Hadel (ruled out as "hadelfilms")