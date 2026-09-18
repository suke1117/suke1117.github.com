# EquineAlpha — AI Horse Racing Prediction & Paper Betting System

## 1. Project Overview

本プロジェクトは、JRA-VANデータを基盤とし、LightGBM（LambdaMART）とPlackett-Luceモデルを用いた公営競技予測、およびFractional Kelly基準を用いたペーパーベッティングシステムである。徹底したデータリークの排除、出力確率の厳密な較正、およびドローダウンを極小化する「負けない」資金管理ロジックの構築を至上命題とする。将来的な競輪・競艇への横展開を見据え、データ取得層とモデリング層を分離したモジュラー設計を採用する。

## 2. Architecture & Tech Stack

- **Data Pipeline**: Python, pandas, JRA-VAN JV-Link (Windows環境想定、またはCSVエクスポートデータのパース). レース、馬、騎手、血統マスタの結合。
- **Machine Learning**: LightGBM (`lambdarank` / LambdaMART) によるPairwise順位学習.
- **Probability Modeling**: Plackett-Luceモデルによる着順確率推定 (Permutation probability).
- **Probability Calibration**: scikit-learn `IsotonicRegression` による確率較正.
- **Betting Strategy**: Fractional Kelly Criterion (α=0.1〜0.25) を用いた最適資金配分ロジック.
- **Backtesting**: 未来の情報を一切参照しない厳格な時系列ウォークフォワードテスト・シミュレータ (Paper Betting).

### Directory Layout

```
src/
  common/      定数・設定・ロギング・競技スペック (SportSpec)
  data/        データ取得層 (sources/) と特徴量エンジニアリング (features.py), preprocess.py CLI
  models/      LightGBM ランカー, Plackett-Luce, Isotonic 較正, train_lgbm.py CLI, predict.py
  betting/     Kelly 計算 (kelly_calculator.py CLI), ベット選択戦略 (strategy.py)
  backtest/    ウォークフォワード・ペーパーベッティング simulator.py CLI, パラメータ探索 sweep.py CLI, metrics.py
tests/         pytest (リーク検査・確率整合性・Kelly 制約を含む)
raw_data/      JRA-VAN CSV エクスポート置き場 (git 管理外)
processed/     前処理済み特徴量 (git 管理外)
artifacts/     学習済みモデル・較正器 (git 管理外)
```

## 3. Strict Rules & Engineering Principles

1. **データリークの絶対的排除**:
   - 交差検証において `KFold` や `train_test_split` (shuffle=True) を絶対に使用してはならない。
   - モデルの評価およびペーパー賭けには、常に時間軸を順方向にのみ進める `TimeSeriesSplit` または独自の日付ベースのウォークフォワード検証のみを使用すること。
   - 特徴量は必ず「当該レース開催日より前」の情報のみから計算する（`shift(1)` / as-of 結合）。当該レースの着順・タイム・確定オッズ由来の値を特徴量に混入させてはならない。
2. **ターゲット変数の制約**:
   - 走破タイムの予測（回帰問題）は実装しない。ペース依存のノイズを排除するため、必ず「着順（相対順位）」をターゲットとし、ペアワイズ学習を適用すること。
3. **確率の明示的変換と較正**:
   - LightGBMの出力スコアをそのまま確率として扱ってはならない。Plackett-Luceモデルによる正規化と、Isotonic Regressionによるキャリブレーションを必ず推論パイプラインに組み込むこと。
4. **保守的な資金管理（負けないロジック）**:
   - ベッティングモジュールでは、フル・ケリーを絶対に使用しない。期待値 (EV) がユーザー定義の閾値（例: 1.05）を超える場合のみ発火し、賭け金は Fractional Kelly (デフォルトα=0.1) を上限とする制約をハードコードすること。
   - `src/common/config.py` の `MAX_KELLY_ALPHA` (=0.25) を超える α は例外として拒否する。1レースあたりの投入額は `MAX_RACE_EXPOSURE` (資金の 5%) を超えない。
5. **コマンド駆動モジュール設計**:
   - 各モジュール（前処理、学習、推論、バックテスト）は、独立したPythonスクリプトとしてCLIから実行できるように `argparse` または `click` で構築すること。

## 4. Development Phases & Test Commands

以下の順序で開発を進め、各ステップごとにテストコマンドを実行して健全性を確認せよ。テストに必要なダミーデータやモック機能が必要な場合は適宜生成すること。

- **Phase 1: JRA-VAN Data Parser & Feature Engineering**
  - レーステーブル、馬情報テーブルの結合と、過去成績に基づく時系列特徴量の生成。
  - Test Command: `python src/data/preprocess.py --input raw_data/ --output processed/`
  - (raw_data/ が空の場合は合成データを自動生成する。明示的には `--synthetic`)
- **Phase 2: LightGBM Ranking Model & Calibration**
  - LambdaMARTモデルの訓練と、Isotonic Regressionによる確率較正モジュールの実装。
  - Test Command: `python src/models/train_lgbm.py --data processed/train.csv --model_dir artifacts/`
- **Phase 3: Plackett-Luce & Fractional Kelly Betting Logic**
  - 予測スコアから勝率への変換と、ケリー基準に基づくベットサイズ計算ロジック。
  - Test Command: `python src/betting/kelly_calculator.py --prob 0.15 --odds 10.0 --alpha 0.1`
- **Phase 4: Walk-Forward Backtester (Paper Betting Module)**
  - 過去データを時系列で進めながら、実際のオッズデータと照らし合わせて資金増減をシミュレーションするモジュール。回収率、最大ドローダウン、シャープレシオを出力する。
  - Test Command: `python src/backtest/simulator.py --start_date 2022-01-01 --end_date 2023-12-31`

- **Phase 4b: Betting Parameter Search**
  - EV 閾値と Kelly 係数のウォークフォワード探索。予測は 1 回だけ生成してグリッドで使い回す。
    探索期間とホールドアウト期間を分け、両者の成績差を必ず報告する。
  - Test Command: `python src/backtest/sweep.py --start_date 2021-01-01 --end_date 2023-12-31 --holdout_start 2023-04-01`

### Unit Tests

```
python -m pytest tests/ -q
```

全フェーズの CLI を順に流す統合チェック: `make all` (Makefile 参照)。

## 5. Extension to Keirin / Kyotei

- 競技固有の定数は `src/common/sport.py` の `SportSpec` に集約する（最大出走数、券種、ベット単位）。
- 新しい競技を追加する場合は `src/data/sources/base.py` の `DataSource` を継承し、正規化スキーマ (`src/data/schema.py`) の `races` / `entries` テーブルを返すアダプタを実装するだけでよい。学習・較正・ベッティング・バックテストのコアは競技非依存である。
