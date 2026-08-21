"""
Chooses the video renderer for a niche.

Adding a niche to Remotion is a `settings.json` edit — set `renderer: "remotion"`
and `image_source: "procedural"` on the niche entry. Nothing here changes.

Imports are deliberately lazy so the five ffmpeg niches never load the Remotion
module (and its subprocess/preflight machinery) at all.
"""

import functools
import logging
from typing import Callable

log = logging.getLogger(__name__)


def get_assembler(niche: dict | None, cfg=None, **extra) -> Callable[..., str]:
    """
    Return a callable with `assemble_from_images`' keyword signature.

    `extra` (e.g. title, seed) is bound onto the Remotion renderer only — the
    ffmpeg assembler has no such parameters and must be handed back untouched.
    """
    renderer = (niche or {}).get("renderer", "ffmpeg")

    if renderer == "remotion":
        from pipeline.remotion_renderer import render_with_remotion
        return functools.partial(render_with_remotion, niche=niche, **extra)

    if renderer != "ffmpeg":
        log.warning(
            "Unknown renderer %r for niche %r — falling back to ffmpeg",
            renderer, (niche or {}).get("id"),
        )

    from pipeline.ffmpeg_assembler import assemble_from_images
    return assemble_from_images
