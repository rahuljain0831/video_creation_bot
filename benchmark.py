"""
Phase 0 benchmark — time one full manual video end to end.
Run after Phases 1-4 are implemented. Records timing per step to stdout + logs.
"""
import time
import logging
from pathlib import Path

log = logging.getLogger("benchmark")


def timed(label: str):
    """Context manager that prints elapsed time for a step."""
    class _Timer:
        def __enter__(self):
            self.start = time.perf_counter()
            log.info("START  %s", label)
            return self
        def __exit__(self, *_):
            elapsed = time.perf_counter() - self.start
            log.info("DONE   %s — %.2fs", label, elapsed)
            print(f"{label}: {elapsed:.2f}s")
    return _Timer()


def run_benchmark() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    results: dict[str, float] = {}

    print("=== Phase 0 Benchmark ===")

    with timed("1. Quote generation") as t:
        # TODO: call pipeline.quote_gen.generate()
        pass
    results["quote_gen"] = 0.0

    with timed("2. Background pick + variation") as t:
        # TODO: call pipeline.background.pick()
        pass
    results["background"] = 0.0

    with timed("3. TTS voiceover") as t:
        # TODO: call pipeline.tts.synthesize()
        pass
    results["tts"] = 0.0

    with timed("4. MoviePy assembly") as t:
        # TODO: call pipeline.assembler.assemble()
        pass
    results["assembly"] = 0.0

    total = sum(results.values())
    print(f"\nTotal: {total:.2f}s  |  Est. daily load @ 10/day: {total*10/3600:.2f}h")


if __name__ == "__main__":
    run_benchmark()
