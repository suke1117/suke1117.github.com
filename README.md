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
| ダッシュボード | `python src/web/export_dashboard.py --output web/data.json` |
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

## ダッシュボード

`web/index.html` はバックテストと探索の結果を可視化する静的ページです。`web/data.json` を読み込むだけなので、
バックテストを回すたびに書き出し直せば内容が更新されます。GitHub Pages でそのまま配信できます。

```bash
make dashboard     # data.json の書き出しと、データを埋め込んだ単体 HTML のビルド
```

- `web/index.html` + `web/data.json`: 静的サイト用 (Pages 配信を想定)
- `web/dist/index.html`: データを埋め込んだ単体ファイル (1 ファイルで完結)

表示内容は、資産曲線とドローダウン、ケリー係数 α の感度、期待値閾値の感度、探索期間とホールドアウト期間の成績差、
確率較正の当たり具合、月次収支、オッズ帯別成績、再学習の期ごとの成績、モデル単体の精度、効いている安全装置です。

## 出力

- `artifacts/`: `ranker.txt`, `calibrator.pkl`, `pl_temperature.json`, `market_blend.json`, `metrics.json`, `feature_importance.csv`
- `backtest_results/`: `bets.csv`, `equity_curve.csv`, `periods.csv`, `summary.json`, `wf_predictions.csv`
- `backtest_results/sweep/`: `sweep_grid.csv`, `sweep_summary.json`

## 重要な注意

- 同梱の合成データはパイプラインの動作確認用です。合成データ上の回収率は実際の JRA 市場での成績を何ら保証しません。
- バックテストは **確定オッズ** で精算しています。実運用では締切直前のオッズで購入するため、`--odds_haircut` で保守的に評価してください。
- JV-Link の固定長バイナリの直接パースは未実装です。Windows 上で CSV にエクスポートし `raw_data/` に置く運用を想定しています
  (列名マッピングは `src/data/sources/jravan_csv.py`)。
