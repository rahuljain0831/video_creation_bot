# spin_illusion drill — design (short)

Copy of the "reverse the spin with your mind" format (25.8M-view reel) as a catalog drill.

- Dot shape spins on a vertical axis, orthographic, equal dot size, fixed draw order (no depth
  cue), tilted toward camera (20°, ring 35°) so it reads as 3D yet stays ambiguous.
- Per seed: shape (sphere/ring/cylinder/helix/cube), colour scheme (single/rainbow/split),
  3-5 whole turns in 17s, 150-350 dots. No levels; catalog `no_level` + `badge: "BRAIN CHALLENGE"`.
- Timeline: 0-3s badge + "Reverse the spin with your mind"; 3-20s spin; 20-24s
  "Which way did it spin for you? Comment LEFT or RIGHT" (on screen + voiced). No right answer.
- Approaches: new module `spin_illusion.py` with disc ops (picked); pre-drawn image per frame
  (rejected: more code, no gain).
- Not added to cron; 5 previews (one per shape) for review.
