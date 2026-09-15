# PLAN: Image Quality Upgrade (Local Generation, 2.5GB VRAM Ceiling)

Status: DRAFT — redrafted after original file lost. Not yet integrated into pipeline.

## Constraint (locked)
- Usable VRAM: 2.5GB (4GB total minus OS/Windows overhead)
- Time budget: up to 2 min/image is acceptable — quality/relevance > speed
- No cloud image gen for this track (local-only test, per hardware-ceiling principle)
- Goal: max detail + scene relevance, not throughput

## Approach
Test multiple local pipelines standalone (no pipeline integration) against the same
fixed prompt set. Compare on VRAM peak, time/image, and scene-relevance/detail score.
Winner gets swapped into `video_creation_bot` only after this test — per existing
"wrappers proven standalone before integration" principle.

## Candidate methods to test

| # | Method | Why it's a candidate | Risk |
|---|---|---|---|
| 1 | SD1.5 (fp16), realistic checkpoint (e.g. RealisticVision/DreamShaper) @ 512x768, no upscale | Baseline. Known to fit 2.5GB comfortably | Lower detail ceiling |
| 2 | SD1.5 + Real-ESRGAN upscale to 720p+ | Adds detail via upscale pass, still light | Upscale ≠ new detail, just sharpening |
| 3 | SD1.5 + Hires-fix / tiled diffusion (SD Ultimate Upscale) | Regenerates detail at tile level — more "real" detail than ESRGAN | Slower, more VRAM per tile pass |
| 4 | SD1.5 + ADetailer (face/hand inpaint) + Real-ESRGAN | Fixes SD1.5's worst failure mode (faces/hands) before upscale | Extra pass = more time, still should fit budget |
| 5 | Flux schnell, Q2/Q3 GGUF quant, CPU offload | Test to confirm/deny whether quality survives quant this aggressive | Likely fails quality bar or exceeds 2 min — run once to settle this, don't assume |
| 6 | SDXL Turbo/Lightning, heavily quantized, sequential CPU offload | Stretch test — SDXL usually needs 6GB+, but distilled+quant+offload might squeeze in given generous time budget | May OOM outright — acceptable to fail fast and eliminate |

Methods 1–4 are the expected realistic contenders. 5–6 exist to close the "did we
actually check" gap rather than assume Flux/SDXL are excluded.

## Test protocol
1. Fix a set of 8–10 real scene prompts pulled from actual `video_creation_bot` scripts
   (not synthetic prompts — must reflect real scene-matching difficulty)
2. Run each candidate method against the same prompt set, same seed where applicable
3. Record per method: peak VRAM (nvidia-smi or similar), time/image, OOM y/n
4. Manual relevance/detail score per image, 1–5, scored blind (don't know which method
   produced which image while scoring)
5. Rank by: (a) fits 2.5GB — hard filter, (b) relevance/detail score, (c) time as tiebreak only

## Decision gate
- Do not integrate into pipeline until one method wins the test above
- Winner becomes `generate_image_local()` wrapper, tested standalone again on a larger
  sample before pipeline swap (existing convention)

## Open questions
- What checkpoint for SD1.5 — needs a short shortlist + pick (not yet decided)
- Whether ADetailer runs as separate pass or same ComfyUI graph — decide during test, not before
- Real-ESRGAN model variant (general vs anime vs face-specific) — test with general first
