"""
Real stock-chart images for the stock_market niche — drop-in replacement for
image_gen/pexels/library, selected via `image_source: "chart_gen"` in
settings.json.

Why this exists: the Sept 2026 pipeline audit found AI-generated and
stock-photo images score as "generic, not relevant" for niches where the
picture is supposed to BE the information (a chart), not illustrate a mood.
An AI model asked to "draw a stock chart" invents numbers and shapes; this
module instead pulls real OHLC data via yfinance and renders the actual
indicator/pattern, computed in code — never a label on a chart that doesn't
actually show what it claims to show.

Scope, deliberately narrow: only concepts that are cheaply and *honestly*
verifiable from OHLC data alone (CONCEPT_RECIPES below). Anything needing
real pattern recognition (engulfing candles, head-and-shoulders, doji) is
out of scope for now — faking the label would reproduce the exact relevance
problem this module exists to fix. See CLAUDE.md "stock_market niche" note.
"""

import logging
import random
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


class ChartGenError(RuntimeError):
    """Raised when no usable chart could be produced for a scene."""


# Liquid, long-history tickers only — thin/illiquid symbols produce gappy,
# misleading OHLC data. Mix of US and India (NSE) large-caps + one index each,
# since content is evergreen education, not a specific-market call.
TICKER_POOL = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META",
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "^GSPC", "^NSEI",
]

# Dark, high-contrast palette for phone viewing. Green/red for up/down candles
# is a universal finance-domain convention — the one deliberate exception to
# "assign categorical hues by fixed order," because the audience already reads
# it as polarity, not identity.
BG_COLOR = "#0d1117"
GRID_COLOR = "#21262d"
TEXT_COLOR = "#e6edf3"
MUTED_COLOR = "#8b949e"
UP_COLOR = "#3fb950"
DOWN_COLOR = "#f85149"
ACCENT_COLOR = "#58a6ff"


def _seeded_rng(seed: int) -> random.Random:
    return random.Random(seed)


def _fetch_ohlc(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV via yfinance. Raises ChartGenError on empty/failed fetch."""
    import yfinance as yf

    try:
        df = yf.download(ticker, period=period, interval=interval,
                          progress=False, auto_adjust=True)
    except Exception as e:  # noqa: BLE001 — yfinance raises assorted network errors
        raise ChartGenError(f"yfinance fetch failed for {ticker}: {e}") from e

    if df is None or df.empty:
        raise ChartGenError(f"No OHLC data returned for {ticker} ({period}/{interval})")

    # yfinance sometimes returns MultiIndex columns (ticker, field) even for
    # a single symbol — flatten so downstream code sees plain "Close" etc.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 30:
        raise ChartGenError(f"Only {len(df)} usable rows for {ticker} — too thin to chart")
    return df


def _compute_sma(df: pd.DataFrame, window: int) -> pd.Series:
    return df["Close"].rolling(window).mean()


def _compute_rsi(df: pd.DataFrame, window: int = 14) -> pd.Series:
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


# --------------------------------------------------------------------------
# Finders: each returns a dict describing a REAL, verified occurrence in the
# fetched data, or None if the window contains no honest example — the
# caller then retries with a different ticker/window rather than fabricate one.
# --------------------------------------------------------------------------

def _find_ma_crossover(df: pd.DataFrame) -> dict | None:
    sma20, sma50 = _compute_sma(df, 20), _compute_sma(df, 50)
    diff = (sma20 - sma50).dropna()
    if len(diff) < 2:
        return None
    sign_changes = np.where(np.diff(np.sign(diff.values)) != 0)[0]
    if len(sign_changes) == 0:
        return None
    idx = diff.index[sign_changes[-1] + 1]  # most recent crossover
    bullish = sma20.loc[idx] > sma50.loc[idx]
    return {"sma20": sma20, "sma50": sma50, "cross_idx": idx, "bullish": bool(bullish)}


def _find_rsi_extreme(df: pd.DataFrame) -> dict | None:
    rsi = _compute_rsi(df)
    valid = rsi.dropna()
    if valid.empty:
        return None
    overbought = valid[valid > 70]
    oversold = valid[valid < 30]
    if overbought.empty and oversold.empty:
        return None
    if not overbought.empty and (oversold.empty or overbought.index[-1] > oversold.index[-1]):
        return {"rsi": rsi, "idx": overbought.index[-1], "kind": "overbought"}
    return {"rsi": rsi, "idx": oversold.index[-1], "kind": "oversold"}


def _find_volume_spike(df: pd.DataFrame) -> dict | None:
    vol = df["Volume"]
    rolling_mean = vol.rolling(20).mean()
    ratio = (vol / rolling_mean).dropna()
    if ratio.empty:
        return None
    idx = ratio.idxmax()
    if ratio.loc[idx] < 2.0:
        return None
    return {"idx": idx, "ratio": float(ratio.loc[idx])}


def _find_support_resistance(df: pd.DataFrame) -> dict | None:
    window = df.tail(60)
    if len(window) < 20:
        return None
    resistance = float(window["High"].max())
    support = float(window["Low"].min())
    if resistance <= support:
        return None
    return {"support": support, "resistance": resistance, "window": window}


def _find_gap(df: pd.DataFrame) -> dict | None:
    prev_close = df["Close"].shift(1)
    gap_pct = (df["Open"] - prev_close) / prev_close * 100
    valid = gap_pct.dropna()
    if valid.empty:
        return None
    idx = valid.abs().idxmax()
    pct = float(valid.loc[idx])
    if abs(pct) < 1.5:
        return None
    return {"idx": idx, "pct": pct, "up": pct > 0}


def _find_trend_breakout(df: pd.DataFrame) -> dict | None:
    window = df.tail(40)
    if len(window) < 20:
        return None
    x = np.arange(len(window))
    slope, intercept = np.polyfit(x, window["Close"].values, 1)
    trend = slope * x + intercept
    last_close = window["Close"].values[-1]
    last_trend = trend[-1]
    broke = bool(abs(last_close - last_trend) / last_trend > 0.02)
    return {"window": window, "slope": slope, "intercept": intercept, "broke": broke}


CONCEPT_RECIPES = {
    "candlestick basics": {"finder": None, "title": "Reading a Candlestick"},
    "moving average crossover": {"finder": _find_ma_crossover, "title": "Moving Average Crossover"},
    "rsi overbought oversold": {"finder": _find_rsi_extreme, "title": "RSI"},
    "volume spike": {"finder": _find_volume_spike, "title": "Volume Spike"},
    "support and resistance": {"finder": _find_support_resistance, "title": "Support & Resistance"},
    "gap up down": {"finder": _find_gap, "title": "Price Gap"},
    "trend line breakout": {"finder": _find_trend_breakout, "title": "Trendline Breakout"},
}

CONCEPT_TAGS = list(CONCEPT_RECIPES.keys())


def _match_concept(image_prompt: str) -> str:
    """Find which CONCEPT_RECIPES tag appears in the prompt; default to basics."""
    text = image_prompt.lower()
    for tag in sorted(CONCEPT_TAGS, key=len, reverse=True):
        if tag in text:
            return tag
    return "candlestick basics"


def _draw_candles(ax, df: pd.DataFrame) -> None:
    """Thin-body candlesticks, matplotlib-native — no mplfinance dependency."""
    x = np.arange(len(df))
    width = 0.6
    for i, (_, row) in enumerate(df.iterrows()):
        color = UP_COLOR if row["Close"] >= row["Open"] else DOWN_COLOR
        ax.plot([i, i], [row["Low"], row["High"]], color=color, linewidth=1.2, solid_capstyle="round")
        lo, hi = sorted([row["Open"], row["Close"]])
        height = max(hi - lo, (df["High"].max() - df["Low"].min()) * 0.002)
        ax.add_patch(
            __import__("matplotlib.patches", fromlist=["Rectangle"]).Rectangle(
                (i - width / 2, lo), width, height, facecolor=color, edgecolor=color, linewidth=0,
            )
        )
    ax.set_xlim(-1, len(df))


def _style_axes(ax, fig) -> None:
    ax.set_facecolor(BG_COLOR)
    fig.patch.set_facecolor(BG_COLOR)
    ax.grid(True, color=GRID_COLOR, linewidth=0.6, alpha=0.7)
    ax.tick_params(colors=MUTED_COLOR, labelsize=13)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)
    ax.set_xticks([])


def _render(ticker: str, tag: str, df: pd.DataFrame, found, render_w: int, render_h: int, dest: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dpi = 100
    fig_w, fig_h = render_w / dpi, render_h / dpi
    recipe = CONCEPT_RECIPES[tag]

    has_panel = tag in ("rsi overbought oversold",)
    if has_panel:
        fig, (ax, ax2) = plt.subplots(
            2, 1, figsize=(fig_w, fig_h), dpi=dpi,
            gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08},
        )
    else:
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
        ax2 = None

    window = found["window"] if isinstance(found, dict) and "window" in found else df.tail(60)
    _draw_candles(ax, window)
    _style_axes(ax, fig)

    x = np.arange(len(window))

    if tag == "moving average crossover" and found:
        sma20 = found["sma20"].reindex(window.index)
        sma50 = found["sma50"].reindex(window.index)
        ax.plot(x, sma20.values, color=ACCENT_COLOR, linewidth=2.0, label="SMA 20")
        ax.plot(x, sma50.values, color="#d29922", linewidth=2.0, label="SMA 50")
        ax.legend(loc="upper left", facecolor=BG_COLOR, edgecolor=GRID_COLOR,
                   labelcolor=TEXT_COLOR, fontsize=12)

    elif tag == "rsi overbought oversold" and found and ax2 is not None:
        rsi = found["rsi"].reindex(window.index)
        ax2.plot(x, rsi.values, color=ACCENT_COLOR, linewidth=2.0)
        ax2.axhline(70, color=DOWN_COLOR, linewidth=1.0, linestyle="--")
        ax2.axhline(30, color=UP_COLOR, linewidth=1.0, linestyle="--")
        _style_axes(ax2, fig)
        ax2.set_ylim(0, 100)
        ax2.text(0.01, 0.92, "RSI (14)", transform=ax2.transAxes, color=MUTED_COLOR,
                  fontsize=12, va="top")

    elif tag == "volume spike" and found:
        idx_pos = window.index.get_indexer([found["idx"]])[0] if found["idx"] in window.index else None
        if idx_pos is not None and idx_pos >= 0:
            ax.axvline(idx_pos, color=ACCENT_COLOR, linewidth=1.5, alpha=0.6)

    elif tag == "support and resistance" and found:
        ax.axhline(found["resistance"], color=DOWN_COLOR, linewidth=1.5, linestyle="--")
        ax.axhline(found["support"], color=UP_COLOR, linewidth=1.5, linestyle="--")

    elif tag == "gap up down" and found:
        idx_pos = window.index.get_indexer([found["idx"]])[0] if found["idx"] in window.index else None
        if idx_pos is not None and idx_pos >= 0:
            ax.axvline(idx_pos, color=ACCENT_COLOR, linewidth=1.5, alpha=0.6)

    elif tag == "trend line breakout" and found:
        trend_y = found["slope"] * x + found["intercept"]
        ax.plot(x, trend_y, color=ACCENT_COLOR, linewidth=2.0, linestyle="--")

    label = ticker.replace(".NS", "").replace("^", "")
    fig.text(0.05, 0.94, recipe["title"], color=TEXT_COLOR, fontsize=32,
              fontweight="bold", ha="left", va="top")
    fig.text(0.05, 0.905, label, color=MUTED_COLOR, fontsize=18, ha="left", va="top")

    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, facecolor=BG_COLOR, bbox_inches=None)
    plt.close(fig)


def get_chart_image(
    image_prompt: str,
    niche: dict,
    output_dir: str,
    scene_index: int,
    cfg=None,
    seed: int = 0,
    used_tickers: set[str] | None = None,
) -> str:
    """
    Drop-in replacement for get_pexels_image() / get_library_image(). Picks a
    concept from image_prompt (see CONCEPT_TAGS), finds a REAL occurrence of it
    in a real ticker's data, and renders it. Raises ChartGenError if no ticker
    in the pool yields a genuine example within the retry budget.
    """
    render_w, render_h = 1080, 1920
    if cfg is not None:
        res = getattr(cfg, "video", {}).get("resolution") if hasattr(cfg, "video") else None
        if res:
            render_w, render_h = res[:2]

    tag = _match_concept(image_prompt)
    recipe = CONCEPT_RECIPES[tag]
    rng = _seeded_rng(seed)
    pool = [t for t in TICKER_POOL if not used_tickers or t not in used_tickers] or TICKER_POOL
    rng.shuffle(pool)

    last_err: Exception | None = None
    for ticker in pool[:6]:  # bounded: don't burn the whole pool on one scene
        try:
            df = _fetch_ohlc(ticker)
        except ChartGenError as e:
            last_err = e
            log.warning("chart_gen: %s", e)
            continue

        found = recipe["finder"](df) if recipe["finder"] else {}
        if recipe["finder"] and found is None:
            log.info("chart_gen: no real %r occurrence for %s, trying next ticker", tag, ticker)
            continue

        dest = Path(output_dir) / f"scene_{scene_index:02d}.png"
        try:
            _render(ticker, tag, df, found, render_w, render_h, dest)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("chart_gen: render failed for %s/%s: %s", ticker, tag, e)
            continue

        if used_tickers is not None:
            used_tickers.add(ticker)
        log.info("chart_gen: scene %d -> %s (%s, %s)", scene_index, dest, tag, ticker)
        return str(dest)

    raise ChartGenError(
        f"No usable chart for concept={tag!r} after trying {min(6, len(pool))} tickers"
        + (f" (last error: {last_err})" if last_err else "")
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    prompt = sys.argv[1] if len(sys.argv) > 1 else "candlestick basics"
    path = get_chart_image(prompt, niche={}, output_dir="/tmp/chart_gen_test", scene_index=0, seed=1)
    print(f"Saved: {path}")
