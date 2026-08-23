# Claude × Amazon アフィリエイト 記事生成システム

「Claudeに比較記事を書かせて、置いておく」という運用を、そのまま仕組みにしたものです。

商品データを1か所（`data/`）に置くと、

1. **比較記事**を生成して GitHub Pages に公開し、
2. **記事どうしの回遊**と **Pinterest用の比較表画像**を自動で作り、
3. その記事を **X（旧Twitter）に定期投稿**する

ところまでを GitHub Actions が回します。サーバー代はかかりません。

```
                        ┌─> 比較記事（8構成・リンク位置は固定）
data/comparisons.yml ───┼─> 比較表画像（Pinterest用 1000x1500）
data/products.yml    ───┼─> 回遊リンク（次に迷いそうなこと）
                        └─> X の投稿文（1日4回・#PR付き）
        ▲
        │  docs/prompts/ のプロンプトで Claude に生成させる
        │
    (審査通過後) PA-API ──> 価格・画像・在庫を毎日自動更新
```

## 4STEP との対応

| STEP | 元記事の内容 | このリポジトリでの実装 |
| --- | --- | --- |
| 1 | 放置で売れるジャンルを抽出 | [`docs/prompts/01-genre-research.md`](docs/prompts/01-genre-research.md) |
| 2 | レビューを読ませて比較記事を書かせる | [`docs/prompts/02-comparison-article.md`](docs/prompts/02-comparison-article.md) → `data/*.yml` に貼る |
| 3 | リンクの位置を設計する | `templates/comparison.html` で**位置を固定済み**（記事ごとに考えない） |
| 4 | 回遊と Pinterest | [`docs/prompts/03-related-articles.md`](docs/prompts/03-related-articles.md) ＋ `/pin/<slug>.png` を自動生成 |

## クイックスタート

```bash
pip install -r requirements.txt

python scripts/validate.py              # データの型と表現をチェック
python scripts/build_site.py --local    # public/ にサイトを生成（手元確認用）
python -m http.server -d public 8000    # http://localhost:8000

python scripts/post_to_x.py --dry-run   # X に投稿する文面を確認（投稿はしない）
```

> `--local` は手元確認用に `base_path` を外すオプションです。
> 公開用のビルド（Actions が実行するもの）では付けません。

## 比較記事の型

`templates/comparison.html` が8つのセクションを固定で描きます。
`data/comparisons.yml` に構造化データを入れるだけで、この順に並びます。

| # | セクション | リンク |
| --- | --- | --- |
| 1 | 読者がいま困っている場面 | |
| 2 | この記事が向いていない人（先に外す） | |
| 3 | 選ぶときに見るべき基準3つ | **置かない**（まだ選び終わっていないため） |
| 4 | 比較表（`specs` から自動生成） | |
| 5 | 商品ごとの解説（向いている人／向いていない人） | **各末尾に1つ** |
| 6 | レビューで多かった不満と、その対処 | |
| 7 | 状況別のおすすめ | **再掲** |
| 8 | 買ったあと、最初にやること | |
| ＋ | 次に迷いそうなこと（回遊） | 自分の別記事へ |

## 自動化していない部分

`data/products.yml` の **`own_note`（実際に使って気づいた一言）だけは手で書きます**。
空か雛形のままだと `scripts/validate.py` がエラーで公開を止めます。

生成された本文をそのまま出すサイトは、Amazonアソシエイトの審査でも読者の信用でも不利になります。
1商品につき1〜3行、レビューを読んでも分からないこと（実測値、予想と違った点、
買う前に確認すべきだったこと）を書いてください。

## ディレクトリ

| パス | 役割 |
| --- | --- |
| `data/site.yml` | サイト名・URL・アソシエイトID・広告表示の文言 |
| `data/products.yml` | 商品マスタ。`specs` が比較表の列になる |
| `data/comparisons.yml` | 比較記事の定義（8構成 ＋ 回遊） |
| `data/x_templates.yml` | X投稿の文面テンプレートと投稿間隔 |
| `data/state/x_posted.json` | 投稿履歴（自動更新。手で触らない） |
| `content/posts/*.md` | 商品に紐づかない読み物記事 |
| `docs/prompts/` | Claudeに投げるプロンプト集（STEP1・2・4） |
| `scripts/validate.py` | 記事の型と、書いてはいけない表現のチェック |
| `scripts/build_site.py` | サイト生成（Pinterest画像も含む） |
| `scripts/post_to_x.py` | X自動投稿 |
| `scripts/check_links.py` | 廃番リンクの検出（月1回・Actions） |
| `scripts/fetch_products.py` | PA-APIから商品情報を取得（審査通過後） |
| `scripts/new_item.py` | 商品の雛形を `products.yml` に追記 |

## 動いているワークフロー

| ワークフロー | 頻度 | 内容 |
| --- | --- | --- |
| サイトのビルドと公開 | push時 ＋ 毎日09:00 JST | validate → PA-API更新 → 生成 → Pages公開 |
| Xへの自動投稿 | 1日4回 | 直近7日に投稿していない記事を1本投稿 |
| 商品リンクの生存確認 | 毎月1日 | 廃番を検出したら Issue を作成 |

## セットアップ

初回の設定（GitHub Pages の有効化、Amazonアソシエイト審査、X APIキー、Secrets）は
**[docs/SETUP.md](docs/SETUP.md)** にまとめています。

## 規約まわりで対応していること

- 全ページ上部に「本ページはプロモーションを含みます」を表示（ステマ規制）
- X投稿には必ず `#PR` を付与。`validate.py` が付いていないテンプレートを弾く
- 商品リンクは `rel="nofollow sponsored"`
- `associate_tag` が未設定のうちはタグなしリンクを出力（審査前でも安全に運用できる）
- PA-API の価格は取得から24時間以内のみ表示。それ以外は価格帯のみ
- 「絶対」「必ず」「100%」「確実に」「最安値」「治る」などの表現を `validate.py` が検出
- 同一商品の再投稿は既定で7日以上あけ、文面テンプレートも毎回変える
