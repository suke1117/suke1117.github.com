# Amazon アフィリエイト自動化システム

商品データを1か所（`data/products.yml`）に書くだけで、

1. **レビューサイト**を生成して GitHub Pages に公開し、
2. その記事を **X（旧Twitter）に定期投稿**する

ところまでを GitHub Actions で自動化する仕組みです。サーバー代はかかりません。

```
data/products.yml ──┬──> scripts/build_site.py ──> public/ ──> GitHub Pages
                    │
                    └──> scripts/post_to_x.py ───> X に定期投稿
                              ▲
        (任意) PA-API ────────┘  scripts/fetch_products.py で価格・画像を自動更新
```

## クイックスタート

```bash
pip install -r requirements.txt

python scripts/build_site.py          # public/ にサイトを生成
python -m http.server -d public 8000  # http://localhost:8000 で確認

python scripts/post_to_x.py --dry-run # X に投稿する文面を確認（投稿はしない）
```

## ディレクトリ

| パス | 役割 |
| --- | --- |
| `data/site.yml` | サイト名・URL・アソシエイトID・広告表示の文言 |
| `data/products.yml` | 商品マスタ。**ここが唯一の情報源** |
| `data/roundups.yml` | 「〇〇選」のまとめ記事の定義 |
| `data/x_templates.yml` | X投稿の文面テンプレートと投稿間隔 |
| `data/state/x_posted.json` | 投稿履歴（自動更新。手で触らない） |
| `content/posts/*.md` | 商品に紐づかない読み物記事（Markdown） |
| `scripts/build_site.py` | 静的サイト生成 |
| `scripts/post_to_x.py` | X自動投稿 |
| `scripts/fetch_products.py` | PA-APIから商品情報を取得（キー取得後） |
| `scripts/new_item.py` | 商品の雛形を `products.yml` に追記 |
| `templates/` `static/` | HTMLテンプレートとCSS |

## 商品を1件追加する

```bash
python scripts/new_item.py --asin B0XXXXXXXX --title "商品名" --category "ガジェット"
# data/products.yml に追記されるので、summary / pros / cons / review を書く
python scripts/build_site.py
git add data/products.yml && git commit -m "add: 商品名" && git push
```

push すると Actions がビルドして自動で公開され、以降その商品はX投稿の対象にもなります。

## セットアップ

初回の設定手順（GitHub Pages の有効化、Amazonアソシエイト審査、X APIキーの取得、
Secrets の登録）は **[docs/SETUP.md](docs/SETUP.md)** にまとめています。

## 守っている規約まわり

- 全ページ上部に「本ページはプロモーションを含みます」を表示（ステマ規制対応）
- X投稿には必ず `#PR` を付与（`data/x_templates.yml` の `ad_label`）
- 商品リンクは `rel="nofollow sponsored"`
- `associate_tag` が未設定のうちはタグなしリンクを出力（審査前でも安全に運用できる）
- PA-API の価格は取得から24時間以内のみ表示。それ以外は価格帯のみ表示
- 同一商品の再投稿は既定で7日以上あけ、文面テンプレートも毎回変える
