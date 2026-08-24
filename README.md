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

## UI

読まれるのはスマホ縦持ち・片手・夜間で、使える時間は3〜10分。
設計方針は [docs/ux-target.md](docs/ux-target.md) にまとめています。主なものは4つ。

| | |
| --- | --- |
| **状況セレクター** | 記事の冒頭で状況を1タップ選ぶと、該当商品まで飛んで一時的にハイライトされる。全部読ませない |
| **記事内タブ** | 上部に固定。スクロールに応じて現在地がハイライトされる（読み飛ばし前提の構造） |
| **比較表** | 46rem以下ではカードに組み替わる。**横スクロールさせない** |
| **数値の印** | `highlight` を宣言した列で、有利な向きの値に「最少」「最大」を付ける |

JavaScript が動かなくても、記事の内容と導線はすべて使えます
（セレクターは通常のアンカー、タブはページ内リンクとして機能します）。

やらないこと: ポップアップ、追従バナー、カウントダウン、自動再生、無限スクロール、
重い日本語Webフォント。

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

## 実機を使っていない商品も扱える

商品ごとに `stance` を宣言します。**使っていないこと自体は問題ありません。
使っていないのに使ったように書くことが問題**なので、そこを仕組みで防いでいます。

| | `stance: owned` | `stance: researched` |
| --- | --- | --- |
| 前提 | 実際に使った | 使っていない |
| 必須項目 | `own_note`（気づいた一言1〜3行） | `evidence`（調べた日・レビュー件数・出どころ） |
| 記事の表示 | 「実機を使用」バッジ ＋ 使ってみての枠 | 「実機未使用」バッジ ＋ 根拠の1行 |
| 書けないこと | — | 座り心地・音質など、使わないと分からないこと |

`researched` の商品に「使ってみた」「実際に使って」「私は」などが混ざっていると、
`scripts/validate.py` がエラーで公開を止めます。

記事の冒頭には「この記事の作り方」が自動で入り、
何商品を実際に使ったのかが読者に最初に伝わります。

### researched で価値を出す方法

実機がなくても差がつくのは次の3つです。

1. ★1〜2を30件以上読んで、**同じ不満が何件出ているか**を数える
2. その不満が「商品の問題」か「使い方・環境の問題」かを切り分ける
3. **メーカーが揃えて書いていない仕様の条件を揃える**
   （「明るさ◯lx」の測定距離、「耐荷重◯kg」の取り付け幅など）

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
| `docs/ux-target.md` | UIの設計方針（ターゲットと原則） |
| `docs/genre-pet.md` | **いま進めているクラスタ**（ペット用品6ジャンル） |
| `docs/high-demand-themes.md` | 需要の大きいテーマの並べ直し |
| `docs/genre-candidates.md` | STEP1の実行結果（デスク環境ほか3クラスタ） |
| `scripts/validate.py` | 記事の型と、書いてはいけない表現のチェック |
| `scripts/build_site.py` | サイト生成（Pinterest画像も含む） |
| `scripts/post_to_x.py` | X自動投稿 |
| `scripts/check_internal_links.py` | 生成後の内部リンク切れの検出（ビルドごと） |
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
- 実機未使用（`researched`）の商品に体験表現が混ざっていたらエラーで止める
- 同一商品の再投稿は既定で7日以上あけ、文面テンプレートも毎回変える
