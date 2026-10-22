# Blox Studio production direction: reference Shorts plus current trends (synthesis, 2026-10-03)

**Source tags**
- **[A]** Report A, reference Short `5mf4NHItoQA`
- **[B]** Report B, reference Short `qCJB3FCorws`
- **[T]** Trends report (vidIQ, last 7 to 30 days)
- **[SG]** The earlier mainstream style guide
- **[Code]** What I read in the repo myself, without changing anything
- **[H]** A hypothesis. Nobody observed it; test it.

**Key caveats**
- All visual and audio facts in [A] and [B] come from vidIQ's AI description. Nobody watched or listened to the videos.
- [T] is metadata only.
- Both reference Shorts come from **one channel**. Matching "the feel" too closely could pull us onto one creator's house style. The repo already has a `single_reference_dependence` check in `blox/story/originality.py` [Code], so keep every plan passing it.

---

## 0. Decisions in one screen

| Area | Direction |
|---|---|
| Format | **First-person "storytime hybrid"** [A][B]. The protagonist narrates in the past tense with speech tags, and characters' quoted lines are mixed in. Run two length bands: **Storytime, 45–60 s** (as in the references) and **Skit, 20–35 s** (the biggest view lane in [T]). |
| Pace | Storytime: **4.3–4.8 words/s**. Skit: **3.3–4.0 words/s**. The references measure 5.0 [A] and 4.8 [B]. The [SG] target of 2.2–3.4 is too slow for this style. |
| Voices | Keep Piper LibriTTS as the default. Distinct speakers and pitch per role follow [B]. A cheaper "one-voice" mode follows [A]. Run a bake-off against **Kokoro-82M (Apache-2.0)** before switching anything. |
| Look | One set per video [A][B]. Hair and accessory silhouettes identify characters [A][B]. Faces swap expression on the cut [A][B]. Cuts every 1–2 s [A][B][SG]. Captions are white, bold, outlined, with a yellow active word [B]. |
| Sound | A continuous playful bed [B], stings on the key beats [A][B], a whoosh on most cuts [B], and a short silence before the twist [B][SG]. |
| Hard no's | No guns or shooting, no real brands, other games' IP, real people, trending copyrighted audio, appearance-based roasts or voice cloning. |

---

## 1. Voices

### 1.1 What the references do
- **[A]:** one synthetic young-adult male voice reads every character.
  - Mid pitch, high energy, General American, no effects.
  - About 5.0 w/s with almost no pauses.
  - Characters differ only by "flat pitch shifts".
  - Emotion comes from the script's speech tags ("screamed", "sighed") and from face swaps, not from acting. Shouts get only a small volume lift.
- **[B]:** clearly different voices per role. vidIQ says one performer voices everyone; that is not verified.
  - A smug, crisp, confident narrator who pauses briefly before each comeback.
  - A high, nasal, whiny, loud antagonist.
  - A low, flat, deadpan authority figure with one line.
  - A pitched-up, mischievous kid who delivers the last joke.
  - About 4.8 w/s, gaps almost gone, 2–3 deliberate pauses.
- **Takeaway:** both rely on **the script plus the visuals for emotion**, not vocal acting. That suits Piper, which our own docstring calls a "calm reading voice" [Code: `blox/voice/piper.py`].

### 1.2 Target qualities and starting Piper settings per role
**How to read the table**
- **length_scale:** below 1 is faster.
- **noise / noise_w:** these are Piper's `--noise-scale` and `--noise-w-scale`. Higher gives livelier pitch and timing variation [H]; above about 0.9, check for artefacts.
- **Defaults:** the code currently uses 0.667/0.8 neutral, 0.75/0.9 lively and 0.5/0.6 subdued, and `fast` is a length_scale of 0.9 [Code].
- **Speaker choice:** audition 6–10 LibriTTS speakers per role and choose by ear against the qualities below. Never choose a voice because it resembles a creator.

| Role (cast) | Target qualities | Speaker pick | length_scale | noise / noise_w | Pitch | Gaps, emphasis and volume |
|---|---|---|---|---|---|---|
| **Protagonist and narrator** (Bloxy, or whoever says "I") | Young-adult impression, mid pitch, high energy, crisp, a little smug and deadpan. Narrow range, from confident to amused. General American. 4.5–4.8 w/s. [A][B] | Mid-pitch, clear, bright speaker. Bloxy is currently speaker 60, so re-audition. | 0.76–0.82 for narration, 0.85 for quoted lines | 0.72 / 0.88 | 0 st | 0.05–0.10 s between lines. One 0.4–0.6 s pause before each comeback or twist [B]. +3 dB on `shout` lines [A]. |
| **Rival** (Rook) | Brighter and higher than the protagonist, a bit nasal. Loud brag lines with a slight drawl; whiny when he loses. Goes from entitled to defensive to sour, then decent. [B][SG] | A brighter, higher speaker that clearly contrasts with the protagonist | 0.88–0.95 on brags (the drawl), 0.75 when panicking | 0.75 / 0.92 | +1 to +2 st | Brags at `loud`. Don't mock any real accent. |
| **Little sister** (Dot) | Pitched up, mischievous, blunt short bursts of 1–6 words. Often gets the final punchline. [B][SG] | A lighter or higher speaker | 0.80 | 0.75 / 0.90 | **+3 to +5 st** [B] | Short pre-gap before her button line (0.3 s) [B]. |
| **Teacher, judge or narrator** (Ms. Tally) | Low, flat, deadpan, measured. 1–3 "verdict" lines per video, with authority siding against the villain. [B][SG] | A lower, steadier speaker | 1.00–1.05 | 0.5 / 0.6 (the existing subdued setting) | −1 to −2 st | Normal volume. In rule-world stories she can be the third-person narrator who opens "In this town…" [H]. |
| Sidekick or witness (Pip) | Dry, a half-beat late, understated. Carries the bystander reactions ("Ooh.", "Bro.") [B][SG] | Mid-low speaker. Currently speaker 288. | 0.88 | 0.6 / 0.7 | 0 to −1 st | An extra 0.2 s before reaction lines [H]. |

**One-voice mode [A]**
- The protagonist's speaker reads everything.
- Shift pitch by about ±1–2 st per character (Dot +3).
- Speech tags in the script do the emotional work.
- It is cheaper and more consistent. A/B it against distinct voices [H: distinct voices may build recurring-cast recognition].

### 1.3 Engineering gaps found in the code (nothing changed)
- **There is no pitch shift yet.** `pitch_semitones` exists in the character voice config (`blox/demo.py`), but nothing in the Piper path reads it [Code]. A post-process step is needed (the sample rate below is 22050 Hz; read the actual rate from the model's `.onnx.json`):
  - Formant-preserving: `rubberband=pitch=2^(n/12)`. This needs an ffmpeg built with librubberband, which is GPL. Run `ffmpeg -filters | grep rubberband` to check.
  - Fallback: `asetrate=22050*r,aresample=22050,atempo=1/r`. The fallback shifts formants and sounds chipmunk-like. That is fine for Dot (+3 to +5) but sounds unnatural beyond ±2 st on others [H].
  - Reference ratios: +2 st = 1.122, +4 st = 1.26, −2 st = 0.891.
- **There is no pace fast enough for storytime.** `PACE` stops at `fast` = 0.9 and the planning rate `PACE_WPS['fast']` is 3.3 w/s [Code].
  - Add a `rapid` pace of about 0.76–0.80. Set its planning w/s from measurement (expect about 4.3–4.6) [H].
  - Post-tempo is capped by `max_tempo` = 1.06 (the prefs allow up to 1.15) [Code], so most of the speed has to come from length_scale.
- **Captions may drift at fast pace.** Piper gives no word timestamps; alignment is estimated from voiced regions [Code]. At 4.5–5 w/s, word-by-word yellow highlights [B] may drift [H]. Test sync before relying on them. A free local forced aligner would fix it, but that is a new dependency, so it is the owner's call.
- **Intelligibility gate:** at length_scale 0.72–0.78, LibriTTS (audiobook read speech) may sound rushed rather than energetic [H]. If words smear, settle at about 4.2 w/s.

### 1.4 Can Piper LibriTTS get close?
- **For [A]: largely yes [H].** [A]'s delivery is clean digital TTS with flat character shifts and script-carried emotion. Speed plus speech tags plus face swaps reproduce that approach.
- **For [B]: partly [H].** Piper can reach [B]'s role contrasts (whiny vs deadpan vs kid) through speaker choice and pitch, but not acted emotion. Lean on the narration tags ("he snapped", "she froze"), face swaps and SFX, as [B] itself does.

### 1.5 Is there a better free, offline alternative? Kokoro-82M, as a bake-off candidate, not a switch
- **Why it might fit better [H]:** a modern small model whose prosody is generally more natural and upbeat than LibriTTS read speech. It has a `speed` parameter, preset US and UK voices that can be blended into new per-character timbres, and runs on CPU.
- **Downsides [H]:**
  - Far fewer voices than LibriTTS's 904 speakers.
  - No child voice, so Dot still needs a pitch shift.
  - It is a new dependency. The current installer only allows downloads from github.com and githubusercontent.com [Code], and Kokoro weights are usually hosted on Hugging Face.
- **Licence points to verify before any commercial use:**
  1. The weights' licence on the official model card (Apache-2.0 as stated).
  2. The model card's statement about its training data and commercial use.
  3. The G2P stack. The phonemiser's licence must be checked, and if it falls back to espeak-ng (GPL-3.0), run it as a separate process, as Piper is run today [Code].
  4. That no preset voice is described as imitating a real person.
  5. The attribution or NOTICE text to add to descriptions, as is done for LibriTTS's CC BY 4.0 today [Code].
- **Other options [H, verify each]:**
  - MeloTTS (MIT): few English voices.
  - Parler-TTS mini (Apache-2.0 code): voices are described in text ("young man, fast, energetic"), which fits our "qualities, not people" rule, but it is heavier and slower.
- **Do not use:**
  - Models with non-commercial weight licences, such as XTTS-v2 under the Coqui Public Model Licence, or CC-BY-NC weights.
  - **Any engine used in zero-shot cloning mode from a reference clip.** Never feed it audio from these or any other creators.
- **Bake-off protocol [H]:**
  - **Script:** one 12-line test script covering narration, a shout, a deadpan verdict, a kid line and a pause before a punchline.
  - **Candidates:** 3 candidates per role from each engine.
  - **Measure:** w/s and intelligibility (owner listening, or local ASR).
  - **Judging:** the owner picks blind.
  - **Switch rule:** change the default only if Kokoro wins at least 3 of the 4 roles **and** the licence check passes.

---

## 2. Scripts

### 2.1 Format and density
| | Storytime (45–60 s) | Skit (20–35 s) |
|---|---|---|
| Source | [A] 56 s, [B] 73 s; story breakouts in [T] run 75–142 s | [T] sibling and family skits at 16–36 s (Mashles 33M views, Hawks RBX 8–13.5M) |
| Voice | First-person protagonist narrates in past tense with speech tags, plus quoted lines | Mostly dialogue; optionally the protagonist talks to camera [SG] |
| Narration share | 40–60 % of words ([A] 60/40, [B] 40/60) | 0–20 % |
| Words / pace | 200–270 words at 4.3–4.8 w/s | 90–130 words at 3.3–4.0 w/s |
| Lines | About 18–25 quoted lines averaging 5 words, plus about 15–25 narration beats of 6–11 words. That is about 8–9 sentences per 10 s, and a speaker change every 1–2 s. [A][B][SG] | 16–24 lines, 2–8 words each |
| Jokes | One every 3–5 s [A] to 6–8 s [B] | One every 3–4 s [H] |

The [SG] cap of 55–85 words per 25 s is fine for skits. Test storytime against it as a separate arm [H].

### 2.2 Hooks (first 0–3 s)
- The first spoken word comes by 0.3 s [SG]. **The whole premise is in the first sentence** [A][B], and the title repeats it.
- Proven shapes:
  - Huge reaction to a tiny cause [A].
  - An absurd, entitled demand in a public place [B].
  - An impossible rule: "In this town…" [T format 2, 1.2–1.4M views/day].
  - A curiosity or betrayal gap: "He sold my…" [T format 3].

### 2.3 Middle: escalation engine
- **Escalation ladder with resets:**
  - Bring in a new character or a new claim every 8–12 s, and each one resets the scene [A].
  - Build our *own* entrance device: a door chime, a name-tag pop and a sting. **Do not reuse [A]'s "Right then X…" phrasing.**
- **Roast or comeback ladder [B]:**
  - The villain makes about 4–5 status claims, and each gets a one-line comeback.
  - Comebacks target the claim or action, **never looks, body or family situation** (this differs from [B]; see section 5).
- **Laugh cues:** bystander reaction lines or shots roughly every 8–12 s ("Pip choked on his juice.") [B].
- **Speech tags carry the emotion** (gasped, froze, snapped, whispered) [A][B]. This is the main workaround for flat TTS.
- **Turn:** the villain states a rule that later backfires, and a neutral judge decides (Ms. Tally) [B].

### 2.4 Endings
- End hard within 1 s of the punchline [SG]. Neither reference has a spoken call to action [A][B].
- Strong buttons:
  - A **mood flip on the same object that started it**, which loops back to the hook [A].
  - A **callback to the villain's own rule** [B].
  - **An unexpected side character's last line**, usually Dot [B].
- Keep the [SG] ending mix per 12 videos: 4 karma or comic, 3 warm, 2 double twist or callback, 2 choice, 1 cliffhanger. The villain always gets a harmless backfire plus a decent moment [SG].

### 2.5 Titles and metadata
- **Observed pattern** [A][B][T]: pronoun + extreme or entitled action + trivial thing, with an ellipsis and an emotion emoji. 💀 is used for comedy or betrayal, 🥹 for wholesome, 😱 or 🤯 for shock. Write our own wording every time.
- **Description:** the references just repeat the title with no hashtags [A][B]. [SG] recommends a question, an honest "original animation" line and 3–5 hashtags. A/B test the two [H].

### 2.6 Twelve original premises
These ride the patterns in [T], [A] and [B]. None uses food escalation, emergency services, weapons, a seat-for-pet dispute, an animal judge or appearance roasts. No trademarks.

| # | Premise (one line) | Emotional lane | Trend ridden | Setting | Cast | Band | New assets |
|---|---|---|---|---|---|---|---|
| 1 | Pip holds "family court" in the bedroom because someone moved his cap one block to the left; Dot judges with a toy gavel, and the culprit is Pip. | Chaotic family comedy (petty overreaction) | Escalation ladder [A]; sibling skit [T] | bedroom | Pip (narrator), Dot, Bloxy | Storytime 45 s | Gavel (optional) |
| 2 | In this town, every broken promise turns your hoodie grey, and Bloxy's has been going grey all week. | Rule-world mystery, then warm (the promises were broken to keep a surprise for Pip) | Rule-world [T fmt 2] | town_street | Bloxy, Pip, Dot | Storytime 50 s | Palette change over time [H] |
| 3 | In this forest the lamps only light for people telling the truth, and Pip just said he isn't scared. | Gentle suspense, then honest and warm | Rule-world [T] | night_forest | Pip, Bloxy, Dot | Storytime 45 s | None (lamp exists) |
| 4 | In Ms. Tally's class, whoever laughs last gets tomorrow off, and Rook has kept a straight face for an hour. | Absurd comedy | Rule-world [T] | classroom | Rook, Dot, Bloxy, Ms. Tally | Skit 30 s | "Laugh meter" overlay |
| 5 | Dot is Pip's loudest hype squad on the sky obby and he keeps missing jumps, until she goes quiet and he realises he can't jump without her. | Relatable sibling comedy, then warm | Sibling skit [T fmt 1] | sky_obby | Pip, Dot | Skit 25–30 s | None |
| 6 | Rook "borrowed" Dot's golden orb and locked it behind a laser door; she has one button and ten seconds. | Underdog heist and justice triumph | Steal-the-prize mechanic, generic [T fmt 4] | sky_obby | Dot, Rook, Pip | Skit 35 s | Orb, pedestal, laser door |
| 7 | Pip sold Bloxy's lucky star badge to Rook for three coins, exactly the entry fee for the race Bloxy couldn't afford. | Betrayal turned warm (misunderstanding) | Betrayal/curiosity twist [T fmt 3] | town_street | Bloxy, Pip, Rook (returns the badge) | Storytime 50 s | None (coin and badge exist) |
| 8 | Rook declares the town's only shady tree "VIP only, one coin per sit", and Ms. Tally reads the old sign at its base. | Entitled person humbled, karma comedy (ends with Dot letting him sit) | Public micro-conflict, villain's rule backfires [B pattern] | town_street | Bloxy (narrator), Rook, Dot, Ms. Tally | Storytime 55 s | None (tree and sign exist) |
| 9 | Rook bets nobody can beat his lava-run record, and Ms. Tally quietly puts down her clipboard. | Status flip and secret identity | Obby time trial [T] plus [SG] running secret | lava_obby | Rook, Ms. Tally, Bloxy, Pip | Skit 30 s | Stopwatch overlay, clipboard |
| 10 | Last one standing on the spring pad wins Ms. Tally's trophy: Rook brought snacks, Pip brought a pillow, Dot brought nothing. | Competition comedy | Challenge [T fmt 7, highest engagement] | studio | All five | Skit 35 s | Elimination timing |
| 11 | Bloxy finds the tiny obby they built with Pip on their very first day, and someone has been secretly fixing it. | Nostalgia and sad, then warm | Nostalgia [T row 12] | sky_obby | Bloxy, Pip | Skit 30 s | None |
| 12 | Ms. Tally shows two doors, one to the next level and one to the lava pit, and Bloxy and Rook pick opposite ones. Which is right? | Choice/quiz loop | "Which one is correct?" [T fmt 5]; [SG] T3 | studio | Ms. Tally, Bloxy, Rook | 8–15 s | Choice overlay |

Before #12, set the made-for-kids audience setting deliberately, because it decides whether comment-bait endings work [SG]. Run every premise through the originality screen and keep `inspiration.note` honest [SG].

---

## 3. Animation and look

### 3.1 Costumes and accessories, in priority order
Both references identify characters by **one strong silhouette item plus an outfit** [A][B]. [B] reports **no hats**: hair, outfit and a held prop do the work. [A] uses hair, helmets, horns and glasses. Our rig has two hair styles and one hat (a cap) [T][SG].

| Priority | Item | Basis | Who |
|---|---|---|---|
| 1 | **More hair meshes:** bun, long, spiky, pigtails, bald or shaved option | [A][B] (hair is the main identifier) | Ms. Tally (bun), Dot (pigtails), Rook (spiky) |
| 2 | **Glasses, then sunglasses** | [A] (glasses), [SG] (Ms. Tally); sunglasses for smug Rook [H] | Ms. Tally, Rook |
| 3 | **Crown** | [T] ("admin/king" role costumes), [SG] (Rook) | Rook's brag moments, heist villain |
| 4 | **Layered tops:** open jacket or overshirt, tie, safety or referee vest, cardigan | [A][B] (outfit layering, a vest on the authority figure) | Rook (jacket), Ms. Tally (cardigan, referee vest) |
| 5 | **Bow and hood** | [SG] (Dot's bow), [T] (hooded costumes for chase or mystery) | Dot; night_forest stories |
| 6 | **Signature held props** | [B] (a held prop identifies the character) [H for our use] | Ms. Tally: clipboard or stopwatch. Rook: coin pouch. Dot: a builder's wrench. |
| 7 | **Role hats:** helmet, chef hat, nurse cap or doctor coat | [T] (role-identity trends). These need settings outside our current list. | Later |

### 3.2 Faces and expressiveness
- **Snap expressions on the cut instead of blending**, to get the decal-swap read [A][B][H].
- Add more extreme presets on top of the 18 we have [Code]: a scream (mouth fully open, eyes about 1.3–1.4), maximum smug, a mischief grin and a "frozen" shock.
- **Do not copy the meme troll face** [B].
- Body cues:
  - head jitter on shock [A]
  - an idle head-bob [B]
  - a stomping walk-off for the losing villain [B]
  - a red anger tint on rage [B]
  - a 0.5–1 s freeze frame on the villain's shock [B]
- Scale: Dot at 0.72 and Ms. Tally at 1.12 so ages read instantly [SG].

### 3.3 Camera and cut rhythm
- **Shot length:** [A] measured about 26 cuts in 56 s, about 2.1 s per shot. [B] ran about 1 s per shot in fast stretches. Target an **average of 1.3–2.0 s**, dropping to about 1 s in roast or escalation runs. Hold 2–2.5 s only on the reveal reaction [SG].
- **Cut on every sentence or speaker change** [A][B]. Mostly shot/reverse-shot and over-the-shoulder; our schema has `over_shoulder_left/right` [Code].
- **Angles:** low on whoever is winning and high on whoever is losing, swapped at the turn [SG]. A high-angle wide for the key action, and a low angle for the final button line [B].
- **Moves:** tilt or insert onto the key prop [A]; a tracking shot for entrances [A]; whip transitions for time jumps [B] (`whip` exists) [Code].

### 3.4 Effects
| Effect | Seen in | Build status [Code] |
|---|---|---|
| Snap zoom or zoom punch on the key prop or face | [A][B] | Not built. Approximate with a 3–4 frame `push_in`, ease `in` [H]. |
| Camera shake or jolt on impact | [A] | A `shake` camera parameter exists |
| Whip pan with motion blur | [B] | `whip` exists; blur [H] |
| Freeze frame, red rage tint | [B] | Not built |
| Screen bounce | [B] | Not built |
| Text pop-ups and emoji | **Not observed** in [A] (explicitly none) or [B] (none reported) | Low priority. Keep only the [SG] hook text (top band, 0–2.5 s) and an optional entrance name-tag [H]. |

### 3.5 Captions
- White bold sans with a **heavy** black outline, **1–3 words** per chunk [A] (up to 4 [B]).
- The **active word turns yellow** [B]. This needs reliable word timing (see 1.3).
- Place them low-centre, but above the Shorts UI zone [A][B][SG].
- Today the outline is `size//9` with DejaVu Sans Bold [Code]. Test a thicker outline, about `size//6` [H].

### 3.6 Backgrounds
- **One set per video** [A][B] keeps Blender cost low.
- Use the current list: sky_obby, lava_obby, town_street, classroom, night_forest, bedroom, studio.
- New sets, in priority order:
  1. **Home interior** (hallway, stairs, living room), because family and sibling skits are the biggest lane [T] and [A] lives in one.
  2. **Public waiting spaces** for "public micro-conflict" stories [B pattern]: a shop counter, a cafeteria, a park.
  3. A clinic, only as our own "spot the odd one out" idea [T]. Don't copy the trending game.
- Daylight city through the windows adds depth cheaply [B].

---

## 4. Sound
- **Music bed:**
  - [B] runs an upbeat, fast, playful bed under the whole video, with a cue at the twist.
  - [A] has a minimal quirky or tense bed with probable stings on the key beats (inferred from `[music]` caption markers).
  - Direction: a **continuous playful bed, ducked under every line** [SG]. Change the cue at each act turn (3–4 cues) [SG].
  - Add a **0.3–0.6 s dropout before the twist or punchline**. [B] pauses before its twist and puts a music cue at 0:56; the [SG] calls this "the cheapest laugh amplifier".
  - Our music is generated procedurally with set tempo and chords [Code: `blox/voice/synth.py`]. Push `playful` to a faster tempo, about 130–150 BPM [H].
- **SFX density:**
  - **Fast stretches:** about one per cut, with a whoosh on almost every cut [B].
  - **Elsewhere:** 10–16 per 25–30 s [SG], scaling up for 45–60 s storytime.
  - Stingers on punchlines [B], gasps on reveals [A][B], footsteps on entrances [A].
  - `fail_horn` no more than once every five videos [SG].
- **SFX to add to the library** (own or CC0 sources only):
  - a cartoon laugh or crowd "ooh" track [B]
  - a door chime for entrances [H]
  - a stomp [B]
  - a freeze-frame "zip" [H]
  - a bonk
- **Rights:** never use recognisable meme samples or chart or trending songs [SG][T]. In [T], many top items depend on trending audio we cannot license.

---

## 5. What not to do
1. **Violence:** no guns, muzzle flashes, or shooting a parent or an officer. [A] does all of these, and it is an advertiser and minor-safety risk. Escalate with harmless props and comic backfires [A][SG].
2. **Real brands:** [A] names a real fast-food chain. Use fictional or generic brands [A][SG].
3. **Other IP:** no other game's prints or characters (for example, the Creeper shirt in [B]). Do not use trending game names (Animal Hospital, Steal an Egg, MM2, Forsaken and so on), other channels' recurring characters (Bacon, JJ and others), or franchise names (Squid Game, Minecraft, PS5 and others) [B][T].
4. **"Roblox" in titles, tags and thumbnails:** both references and [T] use it. For original Blender characters it carries trademark and misleading-metadata risk. That is the owner's decision after reading the brand guidelines; "blocky", "obby" and "animation" are safer [SG].
5. **Real people:** none. For example, the real-creator skit in [T] row 1 [T][SG].
6. **Copying:** do not copy plots, lines, wordplay, character looks, the "Right then X…" device, title wording, thumbnails or the troll face [A][B]. Reuse mechanics only, and pass `single_reference_dependence` [Code].
7. **Voices:** never imitate or clone a creator's voice, and never use reference audio as a cloning prompt. Pick voices by qualities only (section 1).
8. **Mean humour:** no roasts about looks, body, marriage or follower counts. [B]'s ladder includes some of these. Do not use demeaning stereotype labels such as vidIQ's "Karen" tag [B]. No insults; use "goofball" instead [SG].
9. **Misleading content:** no fake unlocks, "true story" claims, or titles the video doesn't deliver [SG].
10. **Over-speeding TTS:** don't push speed until words smear. Intelligibility beats matching 5 w/s exactly [H].
11. **Unvetted licences:** no engines or voices with non-commercial or unclear licences. Keep attribution for LibriTTS (CC BY 4.0) and any Apache-2.0 notices [Code].

---

## 6. Provenance
- **Inputs:**
  - Reports A, B and Trends as relayed. Their connector calls are as listed in each report; all were retrieved 2026-10-03.
  - The style guide at `/tmp/claude-0/-home-user-blow-studios/94335ae1-b0bf-5f99-ba6d-4ddeb4059494/scratchpad/mainstream_style_guide.md`.
- **This synthesis:** 0 connector calls, no files modified. It was produced 2026-10-03.
- **Repo files I read:**
  - `/home/user/blow-studios/blox/voice/piper.py` (PACE, delivery mapping, install hosts, licence)
  - `/home/user/blow-studios/blox/demo.py` (speakers 60 and 288, unused `pitch_semitones`)
  - `/home/user/blow-studios/blox/manifest/schema.py` (framings, sides, moves, transitions, emotions, `PACE_WPS`, SFX and music cues)
  - `/home/user/blow-studios/blox/voice/tts.py` (`fit_line` atempo)
  - `/home/user/blow-studios/blox/prefs.py` (`max_tempo` 1.06, range up to 1.15)
  - `/home/user/blow-studios/blox/captions_layout.py` (font, outline)
  - `/home/user/blow-studios/blox/manifest/compile.py` (camera `shake`)
  - `/home/user/blow-studios/blox/story/originality.py` (`single_reference_dependence`)
  - `/home/user/blow-studios/blox/voice/synth.py` (procedural music)
- **Limits:**
  - Both references are from one channel.
  - All A and B visual and audio claims are AI descriptions.
  - Trends are metadata-only, keyword-biased snapshots.
  - Every Piper, Kokoro, effect and timing number marked [H] needs a listening or render test before it is locked.