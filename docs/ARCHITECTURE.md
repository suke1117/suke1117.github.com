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
| 当日運用 | `src/live/providers/` (取得), `paper_trader.py` (執行), `ledger.py` (記録), `api.py` (配信) | **取得層のみあり** |
| 可視化 | `src/web/export_dashboard.py`, `src/web/build_static.py`, `web/index.html` | なし |
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

## 2b. 特徴量の as-of 保証と縮小推定

特徴量は 2 つの経路で作られ、どちらも当該レース開催日より前の情報しか見ない。

| 経路 | 対象 | 仕組み |
|---|---|---|
| `_as_of_group_stats` | 馬 (1 日 1 走) | 日付順ソート + `shift(1)`、`_same_day_guard` で二重出走を拒否 |
| `_asof_cum` | 騎手・厩舎・条件別・ペア | 日単位に集約してから累積、`merge_asof` で前日以前の値を引く |

騎手は 1 日に複数レースへ騎乗するため、騎乗単位の `shift(1)` では同一開催日の後のレースが
前のレースへ混入する。`_asof_cum` は日単位で境界を切ることでこれを構造的に防ぐ。
JV-Data のエクスポートにレース発走時刻が無いため、日が「過去だと証明できる」最小単位になる。

条件別の値は `_shrunk_rate` による経験ベイズ縮小を必ず通す。

```
cell_rate = (cell_sum + k * parent_rate) / (cell_n + k)
```

親は 1 段上の集計 (騎手 × 競馬場 の親は騎手全体、騎手全体の親は母集団) で、擬似カウント k は
`SHRINK_K` に集約する。母集団の値も as-of で、欠損補完にデータセット全体の統計量を使わない
(当該レースの頭数のように、その時点で既知の値のみ使う)。この 2 点はリークテストが検査している。

## 2c. 寄与の測定

`src/models/ablation.py` は family を 1 つずつ外して再学習し、ホールドアウトの対数損失の差を出す。
シードを変えた複数回の平均を取り、シード分散より小さい差は判定不能として扱う。
特徴量を増やす判断も減らす判断も、この出力を根拠にする。

現時点の制約として測定は 1 ホールドアウト期間のみで、複数ウィンドウ化はロードマップ V4。
1 ウィンドウの結果だけで family を削除すると、探索の選択バイアス (V3) と同じ誤りを犯す。

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

## 7. 改善ロードマップの扱い

`docs/roadmap.yml` は単なるメモではなく、ダッシュボードが描画するデータである。
そのため `load_roadmap` が読み込み時に検証し (必須項目、優先度の値、依存 id の存在、id の重複)、
テストが依存関係の向き (下位優先度に依存していないか) と循環依存を検査する。
壊れた項目はブラウザではなくコマンド実行時に落ちる。

YAML の折り返し (`>-`) は行を空白で連結するため、日本語では文中に隙間が残る。
`normalize_text` が非 ASCII 文字どうしの間の空白のみを除去する (ASCII が隣接する空白は保持する)。

## 8. 当日運用の経路

```
provider.fetch_card(date) ──┐
provider.fetch_odds(date) ──┤
                            ├─▶ 履歴 (date より前だけ) + 当日 ─▶ build_features ─▶ 当日の行
processed/races.csv ────────┘                                              │
processed/entries.csv                                                      ▼
                                              Predictor (PL → Isotonic → blend) ─▶ p_win
                                                                           │
                                              select_win_bets (Fractional Kelly) ─▶ 推奨購入
                                                                           │
                                          ledger.jsonl (追記のみ) + slips/<date>.json
                                                                           │
                                                          api.py ─▶ ダッシュボード「本日の予想」
```

バックテストとの違いは入力の出どころだけで、特徴量・確率・賭け金の決定はすべて同じコードを通る。
これは意図的な制約で、当日だけ別経路にすると、バックテストが検証した対象と実際に動くものが乖離する。

リークの防ぎ方も同じ。`build_today` は履歴を当日より**厳密に前**の日に絞ってから `build_features` を呼ぶ。
テストは、当日の行の as-of カウント (出走回数、騎乗回数、条件別回数) が
全履歴から作った参照テーブルと一致することを検査している。

当日固有の安全装置として `MAX_DAILY_EXPOSURE` (資金の 20%) がある。バックテストではレース間で資金が
動くため自然に制限がかかるが、当日は全レースが同時に未確定になるため、別の上限が必要になる。

## 9. 競輪・競艇への拡張手順

1. `src/data/sources/keirin_csv.py` などに `DataSource` を実装し、`races` / `entries` を返す
   (`entrant_id` = 選手登録番号、`jockey_id` / `trainer_id` は `entrant_id` と同じ値でよい)。
2. `SOURCE_REGISTRY` に登録し、`preprocess.py` にソース選択オプションを足す。
3. `SportSpec` の `win_takeout` や券種を必要に応じて調整する。
4. 以降のフェーズはそのまま動く。
