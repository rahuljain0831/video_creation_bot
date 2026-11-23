# shrink_focus drill — design (short)

Copy of the "How OLD is your vision?" shrinking-circle format, reframed as a score (user decision:
no age, no eye claim).

- 0-2s badge "FOCUS CHALLENGE" + "How small can you see?"; 2-22s circle shrinks 300px -> 2px
  (exponential) and fades toward the background, counter 1 -> 100; 22-26s
  "When did it disappear for you? Comment your number!" (screen + voice). No answer.
- Shrink AND fade: size alone lets everyone reach 100 on a phone.
- Per seed: circle colour, background shade, fade curve exponent 0.6-1.6.
- No levels (`no_level` + `badge`). No tick audio (would need new audio code; add if wanted).
- Not in cron; previews for review.
