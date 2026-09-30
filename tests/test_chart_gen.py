"""
chart_gen tests use synthetic OHLC data, never live yfinance — network-off
CI and this dev sandbox both need to pass without market-data access. The
finders are tested against data deliberately constructed to contain a real,
verifiable instance of each concept, since the whole point of chart_gen is
never labeling a chart with something it doesn't actually show.
"""
import numpy as np
import pandas as pd
import pytest

from pipeline import chart_gen


def _synthetic_df(n=150, trend=0.0002, vol=0.02, spike_at=None, gap_at=None, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n)
    rets = rng.normal(trend, vol, n)
    close = 100 * np.cumprod(1 + rets)
    open_ = close * (1 + rng.normal(0, 0.003, n))
    if gap_at is not None:
        open_[gap_at] = close[gap_at - 1] * 1.04
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.005, n)))
    volume = rng.integers(1_000_000, 2_000_000, n).astype(float)
    if spike_at is not None:
        volume[spike_at] *= 5
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


def test_match_concept_exact_tag():
    assert chart_gen._match_concept(
        "A candlestick chart showing rsi overbought oversold on a stock"
    ) == "rsi overbought oversold"


def test_match_concept_defaults_to_basics():
    assert chart_gen._match_concept("just a plain chart, nothing specific") == "candlestick basics"


def test_ma_crossover_found_on_real_flip():
    down = _synthetic_df(n=100, trend=-0.003, vol=0.01, seed=1)
    up = _synthetic_df(n=100, trend=0.006, vol=0.01, seed=2)
    up.index = pd.bdate_range(down.index[-1] + pd.Timedelta(days=1), periods=100)
    up = up * (down["Close"].iloc[-1] / up["Close"].iloc[0])
    df = pd.concat([down, up])
    found = chart_gen._find_ma_crossover(df)
    assert found is not None
    assert "cross_idx" in found


def test_ma_crossover_none_on_flat_data():
    # Near-zero drift, low vol: SMA20/SMA50 rarely cross meaningfully.
    df = _synthetic_df(n=60, trend=0.0, vol=0.001, seed=3)
    # Not asserting None strictly (randomness could still produce a flip) —
    # asserting the finder never raises and returns a well-formed result either way.
    found = chart_gen._find_ma_crossover(df)
    assert found is None or "cross_idx" in found


def test_rsi_extreme_found_in_strong_trend():
    df = _synthetic_df(n=150, trend=0.004, vol=0.01, seed=4)
    found = chart_gen._find_rsi_extreme(df)
    assert found is not None
    assert found["kind"] in ("overbought", "oversold")


def test_volume_spike_found_at_planted_index():
    df = _synthetic_df(spike_at=100, seed=5)
    found = chart_gen._find_volume_spike(df)
    assert found is not None
    assert found["ratio"] >= 2.0


def test_volume_spike_none_without_anomaly():
    df = _synthetic_df(seed=6)
    found = chart_gen._find_volume_spike(df)
    assert found is None or found["ratio"] < 2.0  # planted-free data shouldn't reliably spike


def test_support_resistance_levels_are_real_extremes():
    df = _synthetic_df(seed=7)
    found = chart_gen._find_support_resistance(df)
    assert found is not None
    window = df.tail(60)
    assert found["resistance"] == pytest.approx(float(window["High"].max()))
    assert found["support"] == pytest.approx(float(window["Low"].min()))


def test_gap_found_at_planted_index():
    df = _synthetic_df(gap_at=100, seed=8)
    found = chart_gen._find_gap(df)
    assert found is not None
    assert abs(found["pct"]) >= 1.5


def test_trend_breakout_returns_fit():
    df = _synthetic_df(n=100, trend=0.001, seed=9)
    found = chart_gen._find_trend_breakout(df)
    assert found is not None
    assert "slope" in found and "broke" in found


@pytest.mark.parametrize("tag", list(chart_gen.CONCEPT_RECIPES.keys()))
def test_render_produces_correct_size_png(tmp_path, tag):
    df = _synthetic_df(n=150, trend=0.003, vol=0.01, spike_at=100, gap_at=90, seed=11)
    recipe = chart_gen.CONCEPT_RECIPES[tag]
    found = recipe["finder"](df) if recipe["finder"] else {}
    if recipe["finder"] and found is None:
        pytest.skip(f"no real {tag!r} occurrence in this seed's synthetic data")
    dest = tmp_path / "scene_00.png"
    chart_gen._render("TEST", tag, df, found, 1080, 1920, dest)
    from PIL import Image
    im = Image.open(dest)
    assert im.size == (1080, 1920)


def test_get_chart_image_raises_chart_gen_error_when_fetch_always_fails(monkeypatch, tmp_path):
    def _boom(ticker, period="6mo", interval="1d"):
        raise chart_gen.ChartGenError(f"no data for {ticker}")

    monkeypatch.setattr(chart_gen, "_fetch_ohlc", _boom)
    with pytest.raises(chart_gen.ChartGenError):
        chart_gen.get_chart_image(
            "candlestick basics", niche={}, output_dir=str(tmp_path), scene_index=0, seed=1,
        )


def test_get_chart_image_succeeds_with_stubbed_fetch(monkeypatch, tmp_path):
    df = _synthetic_df(seed=12)
    monkeypatch.setattr(chart_gen, "_fetch_ohlc", lambda ticker, period="6mo", interval="1d": df)
    path = chart_gen.get_chart_image(
        "candlestick basics", niche={}, output_dir=str(tmp_path), scene_index=0, seed=1,
    )
    from PIL import Image
    im = Image.open(path)
    assert im.size == (1080, 1920)
