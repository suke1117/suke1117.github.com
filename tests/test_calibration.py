import numpy as np
import pytest

from src.models.calibration import WinProbCalibrator, expected_calibration_error


def test_isotonic_calibrator_fixes_overconfidence():
    rng = np.random.default_rng(0)
    n = 20000
    true_p = rng.beta(1, 8, n)
    won = (rng.random(n) < true_p).astype(float)
    over = np.clip(true_p * 1.6, 0, 1)  # model over-estimates
    race_ids = np.repeat(np.arange(n // 10), 10)
    cal = WinProbCalibrator().fit(over, won)
    p_cal = cal.transform(over, race_ids)
    assert expected_calibration_error(p_cal, won) < expected_calibration_error(over, won)
    sums = np.bincount(race_ids, weights=p_cal)
    assert np.allclose(sums, 1.0)


def test_calibrator_is_monotone():
    rng = np.random.default_rng(1)
    x = rng.random(500)
    y = (rng.random(500) < x).astype(float)
    cal = WinProbCalibrator().fit(x, y)
    grid = np.linspace(0, 1, 50)
    out = cal.model.predict(grid)
    assert (np.diff(out) >= -1e-12).all()


def test_calibrator_refuses_tiny_samples():
    with pytest.raises(ValueError):
        WinProbCalibrator().fit(np.array([0.1, 0.2]), np.array([0, 1]))
