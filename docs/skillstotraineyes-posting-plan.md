# Skillstotraineyes — posting strategy plan (2026-10-17)

Status: proposal. Items marked 🔒 touch a locked drill or schedule and need an explicit OK.

## Business view
- **What we have:** about 1,500 views per Reel from non-followers, but only 5 followers. Discovery works and conversion doesn't.
- **Goal for the next 8 weeks:**
  - Keep views per Reel at 1,500 or more while posting more often.
  - Turn 0.04% of viewers into followers, up to 0.3% or more.
  - Get every new drill in front of viewers fast, and keep only the ones that earn views.
- **How it works:** each drill gets a fixed weekly slot, a "series" people can follow. A new drill earns its slot through a 2-week trial. Every Monday the KPI report decides which drills gain or lose slots.

## What the research says (and how sure it is)
| Finding | Source quality | Use it for |
|---|---|---|
| Reels distribution leans on watch time, likes per reach and **sends (DM shares) per reach**. Sends matter most for non-follower reach. | Widely attributed to Mosseri, but the specific weightings come from secondary blogs | CTA: "send to a friend" |
| 3–5 posts a week roughly doubles follower growth against 1–2. Gains flatten after that. | Buffer, 2M posts, correlational | Cadence target 4–6/week |
| A new Reel every ~12h cannibalises the last one. Give each one 24–48h. | Vendor blog; matches our 2 Oct data (1,578 vs 219 when posted 1 minute apart) | One post a day at most |
| Timing within the day matters more than the day. Use the audience's local time. | Sprout (Mar 2026), Postfast (5,191 Reels) | A/B two time windows |
| Instagram allows **5 hashtags** per post since Dec 2025 | Instagram Help Center via Inro | Already shipped |
| Trial Reels (shown to non-followers first) work through the API with `trial_params` (`MANUAL` / `SS_PERFORMANCE`). Eligibility is reportedly about 200–1,000 followers. | Meta changelog (the parameter); thresholds unconfirmed | Testing new drills, once eligible |
| Cross-posting the same video is fine **without a visible watermark from another app**. Shorts work best around 15–40s. | Instagram guidance on watermarks; vendor blogs | YouTube Shorts expansion |
| On TikTok, an unaudited API app can only post privately. | TikTok official docs | TikTok stays manual or deferred |
| The first 1–3s decide the skip. A text overlay of 5–8 words in the first frame helps, since most viewers watch muted. | Creator blogs, directional only | Hook rules per drill |

## 1. Cadence: one Reel a day at most, growing with the drill count
| Phase | Locked drills | Posts/week | Slots |
|---|---|---|---|
| Now | tracking, slow_pursuit | 3 | tracking: 1 midweek + 1 weekend; slow_pursuit: Wed (locked) |
| P1 🔒 | same | 4 | tracking +1 (Mon), since it's the best performer at ~1.8K avg |
| P2 | +1 drill (e.g. colour_plates) | 5 | new drill gets a **trial slot** of 1/week for 2 weeks |
| P3 | 4+ drills | 6–7 | one series per weekday; the best drill gets 2 slots |

Rules:
- At most 1 Reel per day and at least 20h between Reels (fixes the 2 Oct double post).
- In any 2-week window, every locked drill appears at least once. This keeps your "every drill type appears" goal.
- Never post two drills from the same family back to back (`pick_family` already does this).

## 2. Drill lifecycle: trial, then promote or cut
1. **Trial** (2 weeks, 1 slot a week). Measure views, avg watch time, skip rate and sends against the account median.
2. **Promote** if views are at least 1.3× the median: +1 slot a week.
3. **Keep** between 0.7× and 1.3× the median: 1 slot.
4. **Rework** below 0.7× for 4 posts: fix the hook or length first, then cut.
5. Once Trial Reels are eligible, run new drills as `trial_params: SS_PERFORMANCE`. Followers then only see winners.

## 3. Hook and retention rules (template-level; 🔒 for locked drills)
- First frame: challenge text in 5–8 words plus the level badge. Tracking already does this.
- Length: 18–25s. Drills over 30s get trimmed, not posted.
- End on the question and reveal, so the reveal pays off the hook. Make the last frame loop back to the first if possible.
- Each drill's name doubles as the on-video challenge wording (your earlier decision).

## 4. Caption and engagement (🔒 wording for tracking)
- Line 1, which feeds search: keyword plus challenge, e.g. "Eye tracking challenge: can you follow the red ball?"
- CTA rotation, one per post:
  - "Send this to the friend with the sharpest eyes 👀" (sends)
  - "Comment ✅ if you kept it, ❌ if you lost it" (comments)
  - "Save it and try again tomorrow" (saves)
- Series tag in the caption ("Daily Eye Game #14") gives people a reason to follow.
- Keep the disclaimer. No eyesight or health claims.

## 5. Time testing
- Uploads fire only on the upload poll, about 4 times a day: ~10:00, 12:00, 15:30 and 17:00 UTC (15:30, 17:30, 21:00, 22:30 IST).
- Test A = 10:00 UTC (15:30 IST, best so far) against B = 15:30 UTC (21:00 IST, US morning) for 4 weeks, alternating.
- Once insights work, read the top audience cities and keep the winner. The adaptive scheduler (`time_performance`) takes over after 3 samples per slot.

## 6. Platforms
| Platform | Action | Effort |
|---|---|---|
| Instagram | Primary. Profile name, bio and link done; pin the top 3 Reels | none |
| Facebook | Now posts as Reels (shipped 2026-10-17); judge after 2 weeks | none |
| YouTube Shorts | New eye-drills channel. Same 1080×1920 master, no watermark, posted 30–90 min after IG | 1 OAuth + `skillstotraineyes_yt` exclusive account + `publish_platforms` |
| TikTok | Manual upload of the best Reel weekly, or later an API audit | manual |

## 7. Measurement loop (weekly, Monday)
- Telegram KPI (14d) block, per account. **Gap:** per-drill breakdown, which needs the drill family joined from `videos.variation_params`.
- Targets by week 8: views per Reel ≥ 1,500, watch time ≥ 60% of length, skip rate ≤ 35%, followers ≥ 0.3% of views, sends ≥ 0.5% of reach.

## Implementation backlog (small, in order)
1. ≥20h gap between eye-account posts (scheduler, exclusive-niche path), and at most one due eye manifest per poll.
2. Per-drill KPI breakdown in `run_progress_report.py`.
3. Caption CTA rotation and series number in `skillstotraineyes/wording.py` defaults (🔒 tracking wording).
4. Cadence P1: tracking +1 slot (🔒 `run_scheduled_skillstotraineyes.py` day gate).
5. YouTube Shorts eye channel (new account + OAuth).
6. `trial_params` option on `upload_reel()` for trial-slot drills (once eligible).

## Sources
- [Buffer: how often to post (2M posts)](https://buffer.com/resources/how-often-to-post-on-instagram/)
- [Reels ranking signals overview (socialpilot)](https://www.socialpilot.co/de/blog/instagram-reels-algorithm), [posteverywhere](https://posteverywhere.ai/blog/how-the-instagram-algorithm-works)
- [Meta Instagram Platform changelog (trial reels)](https://developers.facebook.com/docs/instagram-platform/changelog), [Ayrshare trial_params](https://www.ayrshare.com/apis/post/social-networks/instagram)
- [Trial Reels explained (Inro)](https://www.inro.social/blog/what-is-a-reel-trial)
- [Sprout Social best times 2026](https://sproutsocial.com/insights/best-times-to-post-on-instagram/), [Postfast 5,191 Reels](https://postfa.st/blog/best-time-to-post-reels-on-instagram)
- [Instagram 5-hashtag cap (Inro)](https://www.inro.social/tools/instagram-hashtag-generator)
- [Cross-posting and watermarks (post-bridge)](https://www.post-bridge.com/blog/does-cross-posting-hurt-engagement)
- [YouTube Shorts algorithm 2026 (vidIQ)](https://vidiq.com/blog/post/youtube-shorts-algorithm/)
- [TikTok Direct Post API (unaudited = private)](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)
- [Hook / first 3 seconds (go-viral)](https://www.go-viral.app/blog/hook-first-3-seconds/)
