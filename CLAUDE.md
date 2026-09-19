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
  models/      LightGBM ランカー, Plackett-Luce, Isotonic 較正, train_lgbm.py CLI, predict.py, 寄与測定 ablation.py CLI
  betting/     Kelly 計算 (kelly_calculator.py CLI), ベット選択戦略 (strategy.py)
  backtest/    ウォークフォワード・ペーパーベッティング simulator.py CLI, パラメータ探索 sweep.py CLI, metrics.py
  web/         ダッシュボード用データ書き出し export_dashboard.py, 単体HTMLビルド build_static.py
  live/        当日運用: providers/ (差し替え可能なデータ取得), paper_trader.py CLI, ledger.py, api.py
web/           静的ダッシュボード (index.html + data.json)。ハンバーガーメニューで5ページ構成
live_data/     当日の出走表・オッズ・結果・台帳 (git 管理外)
docs/roadmap.yml  優先度つき改善タスク (ダッシュボードに表示される)
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
   - 1 日に複数回登場する主体 (騎手・厩舎) の集計は `_asof_cum` を使い、**日単位の境界**で切ること。
     騎乗単位の `shift(1)` は同一開催日の後のレースが前のレースに混入する。
   - データセット全体の統計量 (全レースの平均頭数など) を事前分布や欠損補完に使ってはならない。
     未来のレースが含まれるため。当該レース時点で既知の値を使うこと。
2. **ターゲット変数の制約**:
   - 走破タイムの予測（回帰問題）は実装しない。ペース依存のノイズを排除するため、必ず「着順（相対順位）」をターゲットとし、ペアワイズ学習を適用すること。
3. **確率の明示的変換と較正**:
   - LightGBMの出力スコアをそのまま確率として扱ってはならない。Plackett-Luceモデルによる正規化と、Isotonic Regressionによるキャリブレーションを必ず推論パイプラインに組み込むこと。
   - 2 着・3 着以降の確率に素の Plackett-Luce (Harville) を使ってはならない。強い馬の連対率を系統的に
     過大評価する。`RunDownDiscount` のべき乗ディスカウント (Stern / Henery 型) を較正スライスで推定して通すこと。
     λ=1 は Harville と厳密に一致するので、未推定の状態でも既存の値は変わらない。
4. **保守的な資金管理（負けないロジック）**:
   - ベッティングモジュールでは、フル・ケリーを絶対に使用しない。期待値 (EV) がユーザー定義の閾値（例: 1.05）を超える場合のみ発火し、賭け金は Fractional Kelly (デフォルトα=0.1) を上限とする制約をハードコードすること。
   - `src/common/config.py` の `MAX_KELLY_ALPHA` (=0.25) を超える α は例外として拒否する。1レースあたりの投入額は `MAX_RACE_EXPOSURE` (資金の 5%) を超えない。
   - 当日運用では 1 日の合計投入額が `MAX_DAILY_EXPOSURE` (資金の 20%) を超えない。バックテストはレース間で資金が
     動くため自然に制限がかかるが、当日は全レースが同時に未確定になるため別途上限が要る。
   - 複数券種を同時に買う場合、重なりのある券種 (複勝と馬連と単勝) の同時ケリー厳密解は凸計画であり未実装。
     個別に算出してからレース上限へ比例縮小している。上限を超えることはないが、過小になることはある。
8. **成績の報告には必ず区間を付ける**:
   - 回収率の点推定だけを報告してはならない。`metrics.summarize` が開催日単位のブロック・ブートストラップで
     95% 区間と損益分岐を下回る確率を返すので、それを併記する。
   - 投入額加重と 1 点等重みの回収率を両方出す。複利下では前者が後半の大きいベットに引きずられるため、
     施策の比較には後者を使う。
5. **条件別集計は縮小推定を通す**:
   - 条件別 (騎手 × 競馬場、馬 × 馬場状態、馬 × 騎手 など) の勝率・着順は、セルあたりの標本が
     すぐ枯渇する。生の集計値を特徴量にしてはならない。`_shrunk_rate` で親の値へ縮小すること。
   - 擬似カウントは `SHRINK_K` に集約する。新しい条件を足すときはセルあたりの標本数を確認してから決める。
6. **特徴量は寄与を測ってから採用する**:
   - 新しい特徴量 family を足したら `src/models/ablation.py` で寄与を測り、シード分散より大きいことを確認する。
   - 寄与が測れなかった family は、削除するにしても残すにしても、判断を `docs/roadmap.yml` に記録する。
7. **コマンド駆動モジュール設計**:
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

## 4b. Live Paper Betting

当日運用はバックテストと同じコードを通る。履歴に当日の出走表を追加し、as-of 特徴量を作り直し、
同じ Plackett-Luce と Isotonic を通し、同じ Fractional Kelly で賭け金を決める。
履歴は当日より**厳密に前**の日だけに絞るため、出走馬が自分のレースを見ることはない。

- データ取得は `src/live/providers/` の差し替え可能なプロバイダ。JRA に公開 API は無いため、
  `jravan` (公式有料購読・Windows)、`csv` (自前のファイル)、`http` (任意の JSON、設定ファイルで項目対応)、
  `demo` (履歴の 1 日を再生) の 4 つを用意している。新しい取得元はプロバイダを 1 つ足すだけでよい。
- 台帳 `src/live/ledger.py` は追記のみ。決済時も過去の行を書き換えず、結果を知る前に何を決めたかを残す。
- `src/live/api.py` がダッシュボードと JSON を同一オリジンで配信する。認証は無いのでループバックに限る。

```
python src/live/paper_trader.py bet    --provider demo     # 当日の推奨購入
python src/live/paper_trader.py settle --date <date> --provider demo
python src/live/paper_trader.py status
python src/live/api.py                                     # http://127.0.0.1:8787
```

## 5. Improvement Roadmap

改善タスクは `docs/roadmap.yml` に構造化して管理し、`src/web/export_dashboard.py` 経由でダッシュボードに表示する。
タスクを追加・更新したら `make dashboard` を実行すること。必須項目 (id/title/category/priority/effort/status/impact/why/done)、
優先度の値、依存 id の存在、循環依存は読み込み時とテストで検証される。

優先順位の原則: 測定精度 (P0) → 特徴量 (P1) → モデル・確率層 (P2) → 市場・拡張 (P3)。
測定の信頼区間が施策の効果量より広い状態で下流に進まないこと。

## 5b. 券種と組織の拡張

- 券種は `BetPolicy.ticket_types` で選ぶ (`win` / `place` / `quinella`)。既定は `win` のみ。
  複勝の払戻頭数は出走頭数から決まる (8 頭以上で 3 着まで)。馬連のオッズは実データでは実際のプールを使い、
  無い場合のみ単勝プールから Harville で模擬する (`quinella_odds_from_win_pool`)。模擬値であることを必ず明示すること。
- 複数の主催者を混ぜる場合は `CombinedSource` を使う。race_id の衝突と同日二重出走を結合時に検査する。
  `organizer` は複数組織が混在するときだけ特徴量に加わる (単一組織では定数なので分岐を無駄にする)。
- 週次リターンの天井は「1 点あたり期待対数成長 × 週あたりの点数」で決まる。後者を増やす手段 (地方競馬、券種追加) は
  前者を上げるより現実的なことが多い。`src/backtest/target_planner.py` がこの分解を計算する。

## 6. Extension to Keirin / Kyotei

- 競技固有の定数は `src/common/sport.py` の `SportSpec` に集約する（最大出走数、券種、ベット単位）。
- 新しい競技を追加する場合は `src/data/sources/base.py` の `DataSource` を継承し、正規化スキーマ (`src/data/schema.py`) の `races` / `entries` テーブルを返すアダプタを実装するだけでよい。学習・較正・ベッティング・バックテストのコアは競技非依存である。
