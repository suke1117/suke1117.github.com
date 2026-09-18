#!/usr/bin/env python
"""Bundled inference pipeline: ranker score -> Plackett-Luce -> isotonic calibration.

Library use::

    pred = Predictor.load("artifacts/")
    out = pred.predict(df)          # adds score, p_win_raw, p_win

CLI::

    python src/models/predict.py --data processed/train.csv --model_dir artifacts/ --output artifacts/predictions.csv
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.models.calibration import WinProbCalibrator  # noqa: E402
from src.models.market_blend import MarketBlend  # noqa: E402
from src.models.plackett_luce import PLTemperature, race_win_probs_from_scores  # noqa: E402
from src.models.ranker import RankerModel  # noqa: E402


@dataclass
class Predictor:
    ranker: RankerModel
    temperature: PLTemperature
    calibrator: WinProbCalibrator
    blend: MarketBlend = field(default_factory=MarketBlend)

    def predict(self, df: pd.DataFrame, odds_col: str = "win_odds") -> pd.DataFrame:
        """Add ``score`` (ranker), ``p_win_raw`` (PL), ``p_win_model`` (calibrated) and ``p_win``.

        ``p_win`` equals ``p_win_model`` unless a market blend is enabled and
        ``odds_col`` is present, in which case it is the Benter blend.
        """
        out = df.copy()
        out["score"] = self.ranker.predict(out)
        out["p_win_raw"] = race_win_probs_from_scores(out, "score", self.temperature.temperature)
        out["p_win_model"] = self.calibrator.transform(out["p_win_raw"].to_numpy(), out["race_id"].to_numpy())
        odds = out[odds_col].to_numpy() if (self.blend.enabled and odds_col in out) else None
        out["p_win"] = self.blend.transform(out["p_win_model"].to_numpy(), odds, out["race_id"].to_numpy())
        return out

    def save(self, model_dir: Path) -> None:
        model_dir = Path(model_dir)
        self.ranker.save(model_dir)
        (model_dir / "pl_temperature.json").write_text(json.dumps(self.temperature.to_dict()))
        self.calibrator.save(model_dir / "calibrator.pkl")
        self.blend.save(model_dir / "market_blend.json")

    @classmethod
    def load(cls, model_dir: Path) -> "Predictor":
        model_dir = Path(model_dir)
        return cls(
            ranker=RankerModel.load(model_dir),
            temperature=PLTemperature.from_dict(json.loads((model_dir / "pl_temperature.json").read_text())),
            calibrator=WinProbCalibrator.load(model_dir / "calibrator.pkl"),
            blend=MarketBlend.load(model_dir / "market_blend.json"),
        )


@click.command()
@click.option("--data", "data_path", required=True)
@click.option("--model_dir", default="artifacts/", show_default=True)
@click.option("--output", default="artifacts/predictions.csv", show_default=True)
def main(data_path: str, model_dir: str, output: str) -> None:
    df = pd.read_csv(data_path, parse_dates=["race_date"], dtype={"race_id": str})
    pred = Predictor.load(Path(model_dir)).predict(df)
    cols = ["race_id", "race_date", "entrant_id", "score", "p_win_raw", "p_win_model", "p_win"] + [c for c in ("win_odds", "finish_position") if c in pred]
    pred[cols].to_csv(output, index=False)
    click.echo(f"wrote {output} ({len(pred)} rows)")


if __name__ == "__main__":
    main()
