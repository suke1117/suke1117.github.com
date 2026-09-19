# 実データの入手ルート

現在の数値はすべて合成データによるものです (`src/data/sources/synthetic.py`)。
合成データでは馬の強さも観客の見方も生成器が決めているため、**回収率の水準そのものに意味はありません**。
`--public_noise` を変えるだけで 90% にも 200% にもなります。ロードマップ D1 が、
実データでベースラインを測り直すタスクです。

調査日: 2026-09-19。価格と提供条件は変わるので、契約前に各サイトで確認してください。

## 比較

| | 費用 | Windows | 期間 | JRA / 地方 | 確認状況 |
|---|---|---|---|---|---|
| **地方競馬情報サイト (keiba.go.jp)** | **無料** | 不要 | 要確認 | 地方のみ | 公式に CSV ダウンロード機能あり。列構成は未確認 |
| **JRA-VAN Data Lab.** | 2,090円/月 | **必要** | 1986年〜 | JRA のみ | 公式記載。JV-Link は Windows COM |
| **地方競馬DATA** | 1,980円/月 | 要確認 | 2005年〜 (オッズは2010年2月〜) | 地方のみ | 事業者サイト記載 |
| netkeiba 等のスクレイピング | — | — | — | — | **利用規約で禁止。実装しません** |

### 1. 地方競馬情報サイト (keiba.go.jp) — 無料・公式

地方競馬全国協会の公式サイトに、データダウンロード機能があります。
PC 版の「月別開催日程」ページ下部の「レース情報」「オッズ情報」ボタンから月次 ZIP を取得でき、
中身は出馬表・払戻・レース一覧・オッズの CSV とされています。当日ファイルと月次ファイルの 2 種類。

**まずこれを試す価値が高い理由**: 費用ゼロ、Windows 不要、契約不要。
仮に地方だけでも、平日開催なので**週あたりの購入点数が JRA の 3 倍以上**になります。
週次リターンの天井は「1 点あたりの期待対数成長 × 週あたりの点数」で決まるので、
点数側を増やすのは精度を上げるより現実的です (`src/backtest/target_planner.py` がこの分解を計算します)。

**未確認の点**: このツールの実行環境からは keiba.go.jp に到達できず、CSV の列構成を確認できていません。
とくに **1 頭ごとの確定着順が含まれるか**が未確認です (払戻金は含まれるとされていますが、
払戻だけでは着順ラベルが作れません)。1 か月分の ZIP を `raw_data/` に展開して
`python src/data/preprocess.py --check` を打てば、何が取れて何が足りないかが一度に出ます。

### 2. JRA-VAN Data Lab. — 有料・JRA の本命

月額 2,090円 (税込)、Windows 専用。JV-Link は 32bit の Windows COM コンポーネントなので、
取得時だけ Windows PC か VM が要ります。蓄積系データで 1986 年以降を一括取得でき、
`JVLinkToSQLite` や `jrvltsql` で SQLite / PostgreSQL に落とせるため、
**一度エクスポートすれば以降は Mac / Linux で完結します** (この環境も Linux です)。

実装済みのアダプタ `src/data/sources/jravan_csv.py` は JV-Data のフィールド名を直接読みます。

### 3. 地方競馬DATA — 有料・地方

月額 1,980円。2005年以降、オッズ・票数は 2010年2月以降。
JRA-VAN と組み合わせると `CombinedSource` で JRA + 地方を 1 つのデータセットにできます
(race_id の衝突と同日二重出走は結合時に検査されます)。

### 4. スクレイピング — やりません

netkeiba をはじめ主要サイトは利用規約でスクレイピングを禁止しています。実装しません。

## どのルートでも、入れ方は同じ

列名が JV-Data 形式でなくても、`raw_data/mapping.yml` に対応を書けばコードは触らずに通ります。

```
python src/data/preprocess.py --check --input raw_data/
```

これは何もインポートせず、ヘッダだけを見て「何が認識できて、何が足りなくて、
mapping.yml に何を書けば埋まるか」を出します。データ源が使えるかどうかの判定が、
1 コマンドで済みます。

```yaml
# raw_data/mapping.yml
files:
  races: "*race*.csv"        # 省略時は RA で始まる CSV
  entries: "*result*.csv"    # 省略時は SE で始まる CSV
races:
  開催年月日: race_date       # <CSVの見出し>: <正規化後の名前>
  競馬場コード: venue
entries:
  馬番: post_position
  馬名: entrant_name          # 任意。あれば画面が「3番 <馬名>」になる
values:
  surface: {"芝": turf, "ダ": dirt}   # 芝/ダ/良/稍重/重/不良 は既定で変換済み
```

必須列が揃えば、あとは既存の手順がそのまま動きます。

```
python src/data/preprocess.py --input raw_data/ --output processed/
python src/models/train_lgbm.py --data processed/train.csv --model_dir artifacts/
python src/backtest/simulator.py --start_date <開始> --end_date <終了>
make dashboard
```

`is_synthetic` フラグが自動で `false` になり、ダッシュボードの「合成データによるデモです」の
警告も消えます。合成かどうかの判定は前処理時に記録されるので、取り違えは起きません。

## 必須列と、欠けたときに失われるもの

| 列 | 無いと |
|---|---|
| `race_id` / `race_date` / `venue` / `race_no` | 何も作れない |
| `distance_m` / `surface` / `going` / `race_class` | 条件別の集計 (F6/F7) が全滅 |
| `n_runners` | 複勝の払戻頭数と相対順位が決まらない |
| `entrant_id` / `jockey_id` / `trainer_id` | as-of 集計の主体が無い。過去成績が作れない |
| `post_position` / `draw` | 枠順バイアス (F5) と画面の馬番表示 |
| `age` / `sex` / `weight_carried` | 静的特徴量が痩せる |
| `finish_position` | **学習のラベルが作れない。これが無いと何も始まらない** |
| `win_odds` | 期待値が計算できず、賭けの判断ができない |

任意 (無くても動く): `body_weight` / `body_weight_diff` / `finish_time_sec` / `place_odds` /
`popularity` / `entrant_name` / `jockey_name` / `organizer`。

## 実データを入れたあとに必ず見ること

合成データとの差がそのまま「生成器がどれだけ甘かったか」です。最低限この 3 つを記録します。

1. **較正誤差 (ECE)** — 予測勝率 20% の馬が実際に 20% 勝つか。合成では作りつけで合います。
2. **回収率の 95% 区間** — 点推定ではなく区間。`metrics.summarize` が開催日単位の
   ブロック・ブートストラップで出します。区間が控除率をまたぐなら、まだ何も言えていません。
3. **的中率と平均オッズ** — 合成では人気薄バイアスを生成器が決めています。実データの形は違うはずです。

