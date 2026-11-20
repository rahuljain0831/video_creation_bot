# number_track drill — design (short)

## Intent
Copy the format behind @reflexandfocus's 4.2M-view reel ("follow the ball, which number is it?
comment your number") as a new skillstotraineyes drill. Comments drive reach, so the answer is
never shown.

## Goals / non-goals / anti-goals
- Goal: new catalog drill `number_track`, rendered and reviewed like tracking before any posting.
- Non-goal: changing `tracking` (LOCKED), adding it to the posting cron in this change.
- Anti-goal: revealing the answer on video; medical-claim wording.

## Approaches
- **A (picked):** new module `skillstotraineyes/number_track.py` (same pattern as
  `colour_plates.py`), reusing `sim.make_sim` / `sim.run` and the renderer's existing `text` op.
  Cost: ~30 lines similar to tracking's clear-reveal search.
- B: numbers flag inside `tracking()` — rejected, touches a locked drill.
- C: new function in `drills.py` — rejected, file already 852 lines.

## Decided
| Level | Balls | Ball r | Speed | Bounce time | Extra |
|---|---|---|---|---|---|
| medium | 8 | 44 | 420 | 11.5s | — |
| hard | 8 | 32 | 520 | 15s | — |
| expert | 8 | 26 | 640 | 18.5s | ~3 brief mid-way colour flashes on all balls |

- Level mix 60/30/10 via `pick_track_level()` (easy→medium, god→expert).
- Timeline: 2s still (target red) → 3s moving red → all white, bounce → stop, numbers 1–8
  (seeded shuffle) on every ball → question on screen + voiced, 2.5s hold → end. No reveal.
- Question: "Which number was it? Comment below 👇".
- Target must end clear of other balls (same gap rule as tracking) so the answer is unambiguous.
- Answer number stored in `params` (→ `videos.variation_params`), never in caption.

## Builder's calls
- Target colour red (brand match), per-ball speed spread like tracking, number font size
  ~ball radius, flash colours/timing within expert's bounce window.
- Tests: `tests/test_number_track.py` — determinism on sim state, answer hidden (no reveal ops
  after stop), numbers are a permutation of 1..8, target-gap rule, duration per level.
- Preview renders of all 3 levels for review; not added to cron.
