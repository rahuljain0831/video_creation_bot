# spin_illusion drill — design (short)

Copy of the "reverse the spin with your mind" format (25.8M-view reel) as a catalog drill.

- Dot shape spins on a vertical axis, orthographic, side-on, equal dot size, fixed shuffled draw
  order (no depth cue). Revision 2026-11-20 (user: "too easy"): tilt removed (it handed the
  "seen from above" prior the answer), ring/cube dropped, plus a nested sphere spinning the
  other way, per-dot brightness pulse, and speed drifting ±45% over 6s.
- Review 2: cylinder dropped; nested sphere only inside the sphere, not the helix.
- Per seed: shape (sphere/helix), colour scheme (single/rainbow/split),
  3-5 whole turns in 17s, 150-350 dots. No levels; catalog `no_level` + `badge: "BRAIN CHALLENGE"`.
- Timeline: 0-3s badge + "Reverse the spin with your mind"; 3-20s spin; 20-24s
  "Which way did it spin for you? Comment LEFT or RIGHT" (on screen + voiced). No right answer.
- Approaches: new module `spin_illusion.py` with disc ops (picked); pre-drawn image per frame
  (rejected: more code, no gain).
- Not added to cron; 5 previews (one per shape) for review.
