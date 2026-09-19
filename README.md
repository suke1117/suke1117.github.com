# EquineAlpha — 公営競技予測 & ペーパーベッティング基盤

JRA-VAN データを想定した競馬予測 AI と、Fractional Kelly による「負けない」資金管理を統合したシステムです。
競輪・競艇への横展開を前提に、データ取得層とモデリング層を分離したモジュラー構成になっています。

詳細な設計指針・ルールは [`CLAUDE.md`](CLAUDE.md)、アーキテクチャは [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) を参照してください。

## パイプライン

```
raw_data/ (JRA-VAN CSV)  ──▶  Phase 1 preprocess  ──▶  processed/train.csv
                                   │  as-of 特徴量 (shift(1) のみ、当該レース結果は一切参照しない)
                                   ▼
                              Phase 2 train_lgbm
                                   │  LightGBM lambdarank → Plackett-Luce → Isotonic 較正 → (Benter 市場ブレンド)
                                   ▼
                              Phase 3 kelly_calculator / strategy
                                   │  EV ≥ 閾値 のみ発火、Fractional Kelly (α ≤ 0.25 をハードコード)
                                   ▼
                              Phase 4 simulator (walk-forward paper betting)
                                   │  回収率・最大ドローダウン・シャープレシオ・資産曲線
```

## セットアップ

```bash
pip install -r requirements.txt
```

## 実行コマンド

| Phase | コマンド |
|---|---|
| 1 前処理 | `python src/data/preprocess.py --input raw_data/ --output processed/` |
| 2 学習・較正 | `python src/models/train_lgbm.py --data processed/train.csv --model_dir artifacts/` |
| 3 Kelly 計算 | `python src/betting/kelly_calculator.py --prob 0.15 --odds 10.0 --alpha 0.1` |
| 4 バックテスト | `python src/backtest/simulator.py --start_date 2022-01-01 --end_date 2023-12-31` |
| 4b パラメータ探索 | `python src/backtest/sweep.py --start_date 2021-01-01 --end_date 2023-12-31 --holdout_start 2023-04-01` |
| 推論 | `python src/models/predict.py --data processed/train.csv --model_dir artifacts/` |
| 特徴量の寄与測定 | `python src/models/ablation.py --each --seeds 3` |
| ダッシュボード | `python src/web/export_dashboard.py --output web/data.json` |
| 当日の推奨購入 | `python src/live/paper_trader.py bet --provider demo` |
| ローカル API とダッシュボード | `python src/live/api.py` |
| テスト | `python -m pytest tests/ -q` |
| 全部 | `make all` |

`raw_data/` に JRA-VAN の CSV (`RA*.csv`, `SE*.csv`) が無い場合、Phase 1 は自動的に合成データを生成します
（`--synthetic` で明示、`--public_noise` / `--public_form_weight` で市場の効率性を調整可能）。

## 主なオプション (Phase 4)

| オプション | 既定 | 説明 |
|---|---|---|
| `--alpha` | 0.1 | Kelly 係数。0.25 超は拒否される |
| `--ev_threshold` | 1.05 | 期待値 (p × オッズ) の発火閾値 |
| `--retrain_months` | 3 | 再学習間隔 (ウォークフォワード) |
| `--calib_months` | 6 | 較正・ブレンド用に直近何ヶ月をホールドアウトするか |
| `--odds_haircut` | 0.05 | 確定オッズと購入時オッズの乖離を保守的に見積もる控除 |
| `--no_compound` | – | 初期資金ベースの定額 Kelly (結果の解釈が容易) |
| `--no_market_blend` | – | Benter 型の市場ブレンドを無効化 (純粋モデル確率で賭ける) |
| `--max_stake_yen` | 1,000,000 | 流動性キャップ (1 点あたり上限) |

## 特徴量

as-of 集計 80 本を 10 の family に分けています。すべて当該レース開催日より前の情報だけから作ります。

| family | 内容 |
|---|---|
| entrant | 馬の通算・直近成績、スピード指数、間隔 |
| entrant_cond | 馬 × 芝ダート・距離帯・競馬場・馬場状態・クラス |
| jockey | 騎手の通算・直近90日成績、馬の実力からの残差 |
| jockey_cond | 騎手 × 競馬場・芝ダート・距離帯・馬場状態 |
| pair | 馬 × 騎手の相性 |
| switch | 乗り替わりか継続騎乗か、騎手の格の上下 |
| trainer / trainer_jockey | 厩舎の成績、厩舎 × 騎手 |
| static / relative | レース条件、出走馬の中での相対順位 |

### 条件別集計は縮小推定を通す

条件を細かく割るとセルあたりの標本が足りなくなります。実 JRA 規模でも騎手 × 競馬場 × 芝ダ × 距離帯は
5 年で 1 セル 16 騎乗程度、馬 × 騎手に至っては中央値 1 騎乗です。そのままの勝率はノイズなので、
条件別の値はすべて経験ベイズ縮小 (`SHRINK_K`) で親の値に寄せています。標本が少ないセルほど親に近づきます。

### 騎手の勝率は交絡している

騎手の勝率は「騎乗が上手い」と「良い馬に乗せてもらえる」が混ざった数字です。
そのため、馬自身の通算成績から期待される着順に対する残差 (`jky_resid`) を別に持たせています。

### 寄与は測ってから採用する

`src/models/ablation.py` が family を 1 つずつ外して再学習し、ホールドアウトの対数損失の差を出します。
シードを変えた複数回の平均で、ばらつきより小さい差は判定不能として扱います。

```bash
make ablation
```

合成データでの測定では entrant_cond が +0.067、jockey_cond が +0.019 で、この 2 つが寄与しています。
一方 pair (+0.001) と switch (-0.004) は効果が測れませんでした。合成データ側に相性の構造は入れてあるので、
これは「相性が存在しない」ではなく「ペアあたり中央値 1 騎乗では推定できない」という結論です。

## パラメータ探索 (Phase 4b)

EV 閾値と Kelly 係数をウォークフォワードで探索します。ウォークフォワード予測は**一度だけ**生成してグリッド全体で使い回すため、
モデル再学習は 108 通りの組み合わせでも 1 回分で済みます。探索期間で選んだパラメータを、グリッドが一度も見ていないホールドアウト期間で再評価し、
両者の差を必ず表示します。ドローダウン上限 (`--max_dd`) とベット数下限 (`--min_bets`) を満たさない組み合わせは、利益に関わらず失格にします。

合成データでの探索結果 (2021-01〜2023-03 で探索、2023-04〜2023-12 で検証):

| 指標 | 探索期間 | ホールドアウト |
|---|---|---|
| 回収率 | 140.3% | 115.4% |
| 最大ドローダウン | 6.0% | 4.3% |
| シャープレシオ | 2.74 | 1.92 |

選ばれたのは α=0.02、EV≥1.30、1レース1点でした。α を 0.02 から 0.25 に上げると収益は増えますが、
最大ドローダウンは 6% から 52.5% へと収益より速く悪化します。これが Fractional Kelly を採用する理由です。

## 当日のペーパー賭け

バックテストと同じコードで当日の出走表を処理します。履歴を当日より前の日に絞ってから特徴量を作り直すので、
出走馬が自分のレースを参照することはありません。

```bash
python src/live/paper_trader.py bet --provider demo     # 推奨購入を出して台帳に記録
python src/live/paper_trader.py settle --date <date> --provider demo
python src/live/paper_trader.py status
python src/live/api.py                                  # http://127.0.0.1:8787
```

### データの取り込み

JRA に公開 API はありません。使える経路は次の 3 つで、どれを選んでもその先の処理は同じです。

| プロバイダ | 使う場面 | 必要なもの |
|---|---|---|
| `--provider jravan` | 公式の有料購読 | JRA-VAN Data Lab. の契約、Windows、JV-Link、pywin32 |
| `--provider csv` | 購読が無くても今日から動かす | `live_data/cards/<date>.csv` と `live_data/odds/<date>.csv` |
| `--provider http` | 任意の JSON API | `live_data/provider.yml` で URL と項目名を対応づける (`provider.yml.example` 参照) |
| `--provider demo` | 動作確認 | 履歴の 1 日を再生する。購読も設定も不要 |

CSV の雛形は `python src/live/paper_trader.py template --date <date>` で作れます。

### 台帳

`live_data/ledger.jsonl` は追記のみです。決済しても過去の行は書き換えないので、
結果を知る前に何を決めたかが必ず残ります。編集できる賭け記録は、負けた後に編集してしまうものだからです。

### 安全装置 (当日運用)

1 日の合計投入額は資金の 20% (`MAX_DAILY_EXPOSURE`) を超えません。バックテストはレース間で資金が動くため
自然に制限がかかりますが、当日は全レースが同時に未確定になるため別の上限が要ります。
同じ日に二重に賭けることも台帳がブロックします。

### API

`python src/live/api.py` がダッシュボードと JSON を同一オリジンで配信します。認証はないのでループバック限定です。

| エンドポイント | 内容 |
|---|---|
| `GET /api/health` | 稼働状況と、どのデータが揃っているか |
| `GET /api/today?date=` | 当日の全出走馬の確率・オッズ・期待値・推奨購入額 |
| `GET /api/ledger?limit=` | ペーパー賭けの記録 |
| `GET /api/summary` | 資金、回収率、日ごとの履歴 |
| `GET /api/slips` | 予想を作成済みの日付一覧 |

## ダッシュボード

`web/index.html` はハンバーガーメニューで 5 ページに分かれた静的ページです。`web/data.json` を読み込むだけなので、
バックテストを回すたびに書き出し直せば内容が更新されます。GitHub Pages でそのまま配信できます。

| ページ | 内容 |
|---|---|
| 概要 | 成績 KPI、資産曲線、ドローダウン |
| 本日の予想 | ローカル API から当日の出走表と推奨購入を取得 (API 未起動時は手順を表示) |
| 成績の内訳 | α と期待値閾値の感度、探索とホールドアウトの差、月次・オッズ帯別・期別 |
| モデルの中身 | 確率較正、特徴量 family ごとの寄与、モデル単体の精度、安全装置 |
| 改善ロードマップ | 優先度つきタスク |

「本日の予想」だけはライブのデータを読むため、ローカル API 経由で開く必要があります。
公開されているダッシュボードは静的なスナップショットです。

```bash
make dashboard     # data.json の書き出しと、データを埋め込んだ単体 HTML のビルド
```

- `web/index.html` + `web/data.json`: 静的サイト用 (Pages 配信を想定)
- `web/dist/index.html`: データを埋め込んだ単体ファイル (1 ファイルで完結)

表示内容は、成績 KPI、[改善ロードマップ](docs/roadmap.yml)、資産曲線とドローダウン、ケリー係数 α の感度、
期待値閾値の感度、探索期間とホールドアウト期間の成績差、確率較正の当たり具合、月次収支、オッズ帯別成績、
再学習の期ごとの成績、モデル単体の精度、効いている安全装置です。

## 改善ロードマップ

優先度つきのタスクは `docs/roadmap.yml` で管理し、ダッシュボードに表示されます。編集して `make dashboard` を実行すれば反映されます。
`src/web/export_dashboard.py` が読み込み時に必須項目・優先度・依存関係を検証するため、壊れた項目はダッシュボードではなく実行時に落ちます。

| 優先度 | 方針 | 件数 |
|---|---|---|
| P0 | 測定の土台。これが無いと以降の施策の効果を判定できない | 4 |
| P1 | 特徴量。実データ上で精度を最も大きく動かす | 6 |
| P2 | モデルと確率層。同じ特徴量からより多くを引き出す | 5 |
| P3 | 市場と拡張。実際に買える価格での検証と、券種・競技の拡大 | 4 |

P0 を先頭に置いているのは、現在の回収率 116.4% の 95% 信頼区間が [103.9%, 128.8%] と幅 ±12 ポイントあり、
1 点等重みで測り直すと 108.1% (t=1.69) で有意に届かないためです。数ポイントの改善を判定できる測定精度が先に要ります。

## 出力

- `artifacts/`: `ranker.txt`, `calibrator.pkl`, `pl_temperature.json`, `market_blend.json`, `metrics.json`, `feature_importance.csv`
- `backtest_results/`: `bets.csv`, `equity_curve.csv`, `periods.csv`, `summary.json`, `wf_predictions.csv`
- `backtest_results/sweep/`: `sweep_grid.csv`, `sweep_summary.json`

## 重要な注意

- 同梱の合成データはパイプラインの動作確認用です。合成データ上の回収率は実際の JRA 市場での成績を何ら保証しません。
- バックテストは **確定オッズ** で精算しています。実運用では締切直前のオッズで購入するため、`--odds_haircut` で保守的に評価してください。
- JV-Link の固定長バイナリの直接パースは未実装です。Windows 上で CSV にエクスポートし `raw_data/` に置く運用を想定しています
  (列名マッピングは `src/data/sources/jravan_csv.py`)。
