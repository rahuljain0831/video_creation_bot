# number_track Drill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New catalog drill `number_track`: follow the red ball, balls stop numbered 1–8, answer never revealed.

**Architecture:** New module `skillstotraineyes/number_track.py` (pattern of `colour_plates.py`), registered in `drills.BUILDERS` via a late-import shim and in `catalog.json`. Entry point gives it tracking's level mix.

**Tech Stack:** Python, numpy, existing `sim.py` physics, existing renderer ops.

**Spec:** `docs/superpowers/specs/2026-11-20-number-track-design.md`

## Global Constraints
- `tracking()` and `TRACK_LOCKED` are not modified.
- Levels: medium (8, r44, 420, 11.5s), hard (8, r32, 520, 15s), expert (8, r26, 640, 18.5s, ~3 colour flashes). easy→medium, god→expert.
- Question: "Which number was it? Comment below 👇". No reveal op (no RED, no ring) after balls stop.
- Not added to `scheduled-skillstotraineyes.yml`.

## Review Focus
- Number labels overlapping balls / unreadable at r26 → font size tied to ball_r, black stroke (renderer `text` already strokes).
- Hook text wraps over the arena during the first 2.5s → reuse `_text_ops` with badge layout, same as tracking.
- Flash colours include RED → would point at the target; flashes must exclude RED.
- `--level easy/god` forced → must map to medium/expert, not raise.
- Answer leaking via voice line → voice list only hook + question.

---

### Task 1: number_track builder + wiring

**Files:**
- Create: `skillstotraineyes/number_track.py`
- Modify: `skillstotraineyes/drills.py` (BUILDERS shim only), `skillstotraineyes/catalog.json` (new entry), `run_skillstotraineyes.py:226` (`family in ("tracking", "number_track")`)
- Test: `tests/test_skillstotraineyes_number_track.py`

**Interfaces:**
- Produces: `number_track(seed: int, text: dict | None = None, level: str | None = None, entry_cfg: dict | None = None) -> Drill`; `params` includes `family`, `level`, `n`, `ball`, `speed`, `move`, `gap`, `answer` (int 1..8), `flashes` (int).

- [ ] **Step 1: Write failing tests**: determinism (same seed → same params and same ops at a mid frame); per level n/ball/speed/move match the table; numbers at the final frame are a permutation of 1..8 with `params["answer"]` equal to the target's label; no RED disc and no ring in last-frame ops; expert has `flashes == 3`, others 0, and no flash colour equals RED; easy/god accepted; question text in final frame; `build("number_track", 1)` works via catalog.
- [ ] **Step 2: Run** `pytest tests/test_skillstotraineyes_number_track.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** search loop like tracking (make_sim + per-ball 0.6–1.4 speed factor ×1.8, keep best min-gap, want ≥2.2 radii), timeline 2s still / 3s red moving / white bounce / 2.5s numbered hold. Labels: `("text", str(k), x, y, round(ball_r * 1.1), BG)` on WHITE discs. Expert flashes: 3 windows of 0.4s evenly inside the white phase, each ball a seeded colour from `ACCENTS`.
- [ ] **Step 4: Run tests** → PASS; `pytest -m "not slow" -q tests/test_skillstotraineyes_*` → no regressions.
- [ ] **Step 5: Preview renders** `python run_skillstotraineyes.py --family number_track --level {medium,hard,expert} --seed 1 --no-publish --no-llm` style direct render to `output/skillstotraineyes/preview/number_track_{level}.mp4` (no DB row).
- [ ] **Step 6: Commit** on branch `drill/number-track`, merge to main, push.
