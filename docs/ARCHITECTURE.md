# EquineAlpha アーキテクチャ

## 1. レイヤ構成

| レイヤ | モジュール | 競技依存 |
|---|---|---|
| データ取得 | `src/data/sources/` (`DataSource` 抽象クラス, `JRAVanCSVSource`, `SyntheticSource`) | **あり** (アダプタで吸収) |
| 正規化スキーマ | `src/data/schema.py` (`races` / `entries`) | なし |
| 特徴量 | `src/data/features.py` | なし (jockey/trainer が entrant と同一なら自動スキップ) |
| 順位学習 | `src/models/ranker.py` (LightGBM `lambdarank`) | なし |
| 確率化 | `src/models/plackett_luce.py` | なし |
| 較正 | `src/models/calibration.py` (Isotonic) | なし |
| 市場ブレンド | `src/models/market_blend.py` (Benter 条件付きロジット) | なし |
| 資金管理 | `src/betting/kelly_calculator.py`, `src/betting/strategy.py` | なし |
| 検証 | `src/backtest/simulator.py`, `src/backtest/sweep.py`, `src/backtest/metrics.py` | なし |
| 競技定数 | `src/common/sport.py` (`SportSpec`: 競馬 18 頭 / 競輪 9 車 / 競艇 6 艇) | 定義のみ |

## 2. リーク防止の仕組み

1. `build_features` は `(race_date, race_id)` でソートした後、`groupby(key).shift(1)` と累積統計のみで特徴量を作る。
   当該レースの `finish_position` / `finish_time_sec` / `win_odds` は `FORBIDDEN_FEATURE_COLUMNS` として
   `RankerModel.__post_init__` と `assert_no_forbidden` で二重にブロックされる。
2. 同一エントラントが同一日に 2 回出走するデータは例外で拒否する (競輪・競艇で複数走がある場合は
   日付+節のキーを使う)。
3. `tests/test_features_leak.py::test_future_results_do_not_change_past_features` は、未来のレースを削除しても
   過去レースの特徴量がバイト単位で一致することを検証する。
4. `RankerModel.fit` は検証スライスが学習スライスより厳密に後であることを要求する。
5. `WalkForwardSimulator` は各期間で `train < calib < bet period` の時間順序を `assert` し、
   テストはモンキーパッチでその境界を検査する。

## 3. 確率パイプライン

```
score_i = LightGBM(features_i)                       # lambdarank, 1 query = 1 race
p_raw_i = exp(score_i / T) / Σ_j exp(score_j / T)     # Plackett-Luce, T は較正スライスで最尤推定
p_model_i = Isotonic(p_raw_i) / Σ_j Isotonic(p_raw_j) # 単調較正 + レース内正規化
p_i ∝ exp(a·log p_model_i + b·log p_market_i)         # Benter ブレンド (任意, 較正スライスで a,b を最尤推定)
```

`plackett_luce.py` は同じ `p` から馬単 / 馬連 / 3 連単 / 3 連複 / 複勝 (top-k) の確率を導出できるため、
券種ごとのオッズデータが揃えば期待値計算にそのまま使える。

## 4. 資金管理

- 単勝は排反事象なので、1 レース複数点買いは独立 Kelly ではなく **多出目 Kelly の厳密解**
  (`strategy.multi_outcome_kelly`) で配分する。
- 安全装置 (すべて `src/common/config.py` にハードコード):
  - `MAX_KELLY_ALPHA = 0.25` (超過は `KellyAlphaError`)
  - `MAX_SINGLE_BET_FRACTION = 0.03`, `MAX_RACE_EXPOSURE = 0.05`
  - `MIN_WIN_PROB_TO_BET = 0.02`, `MAX_STAKE_YEN = 1,000,000`
  - `DEFAULT_EV_THRESHOLD = 1.05`

## 5. ウォークフォワード

```
|<-------- train (< calib_start) -------->|<- calib (calib_months) ->|<- bet period (retrain_months) ->|
```

期間ごとにランカーを再学習し、直近 `calib_months` を early stopping・温度・較正・ブレンドの推定に使う。
ベット額は各レース直前の残高で決める (`--no_compound` で初期残高固定)。

## 6. 2 段階シミュレータとパラメータ探索

`WalkForwardSimulator` は意図的に 2 段階に分かれている。

| 段階 | メソッド | 依存するもの | コスト |
|---|---|---|---|
| 予測生成 | `generate_predictions` | データと再学習スケジュールのみ (ベット方針に非依存) | 高い (期間ごとに再学習) |
| ベット精算 | `simulate` | ベット方針 (`BetPolicy`) | 低い |

この分離により、`sweep.py` は予測を 1 回生成するだけで 100 通り以上のポリシーを評価できる。
また「予測がベット方針に依存しない」という性質自体が、パラメータ探索によるリークを構造的に防いでいる。

探索は次の順序で行う。

1. 期間全体を探索部とホールドアウト部に時系列分割する。
2. 探索部のみでグリッドを評価し、`--max_dd` / `--min_bets` を満たさない点を失格にする。
3. 残った中から目的関数 (シャープレシオ / 対数成長率 / 回収率) で 1 点を選ぶ。
4. 選んだ 1 点をホールドアウト部で再評価し、探索部との差を必ず出力する。

## 7. 競輪・競艇への拡張手順

1. `src/data/sources/keirin_csv.py` などに `DataSource` を実装し、`races` / `entries` を返す
   (`entrant_id` = 選手登録番号、`jockey_id` / `trainer_id` は `entrant_id` と同じ値でよい)。
2. `SOURCE_REGISTRY` に登録し、`preprocess.py` にソース選択オプションを足す。
3. `SportSpec` の `win_takeout` や券種を必要に応じて調整する。
4. 以降のフェーズはそのまま動く。
