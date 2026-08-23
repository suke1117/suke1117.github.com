# セットアップ手順

上から順にやれば動きます。**1〜3 まででサイトは公開できます。**
Amazonアフィリエイトの収益化（4以降）は審査が必要なので、先にサイトを育てるのが順番です。

---

## 1. リポジトリ名について（重要）

現在のリポジトリ名は `suke1117.github.com` です。
GitHub Pages の「ユーザーサイト」として `https://suke1117.github.io/` で公開するには、
リポジトリ名が **`suke1117.github.io`**（`.io`）である必要があります。

| 選択 | 公開URL | `data/site.yml` の設定 |
| --- | --- | --- |
| このまま使う | `https://suke1117.github.io/suke1117.github.com/` | `base_url: "https://suke1117.github.io"` / `base_path: "/suke1117.github.com"` |
| `.io` にリネーム（推奨） | `https://suke1117.github.io/` | `base_url: "https://suke1117.github.io"` / `base_path: ""` |
| 独自ドメイン | `https://example.com/` | `base_url: "https://example.com"` / `base_path: ""` |

リネームは Settings → General → Repository name から。リネーム後は `base_path` を空にしてください。

> 独自ドメインを使う場合は、Amazonアソシエイトの審査でも有利に働きます（サイトの継続運営意思が伝わるため）。

---

## 2. GitHub Pages を有効にする

1. リポジトリの **Settings → Pages** を開く
2. **Source** を `GitHub Actions` に設定する（`Deploy from a branch` ではない）
3. **Settings → Actions → General → Workflow permissions** を
   `Read and write permissions` にする（キャッシュや投稿履歴のコミットに必要）

---

## 3. サイトを公開する

`main` ブランチに push すると、`.github/workflows/build-and-deploy.yml` が動いて公開されます。
手動で動かす場合は Actions タブ →「サイトのビルドと公開」→ Run workflow。

公開前にローカルで確認するには:

```bash
pip install -r requirements.txt
python scripts/build_site.py
python -m http.server -d public 8000
```

### コンテンツを増やす

Amazonアソシエイトの審査では**サイトに十分なオリジナル記事があること**が見られます。
目安として、申請前に **10記事以上**は用意してください。

- 商品レビュー → `data/products.yml` に追記（`scripts/new_item.py` が雛形を作ります）
- まとめ記事 → `data/roundups.yml` に追記（商品のslugを並べるだけ）
- 読み物 → `content/posts/*.md` に Markdown を追加

いずれも、**他サイトのコピーではない自分の文章**であることが重要です。
`cons`（気になった点）を必ず書く構成にしているのは、独自性を担保するためです。

---

## 4. Amazonアソシエイトに申し込む

1. https://affiliate.amazon.co.jp/ から申し込む
2. 申請フォームの「ウェブサイト」に、3で公開したサイトのURLを入れる
3. 審査通過後、**180日以内に3件の適格販売**を達成すると本登録になる
4. 本登録後、トラッキングID（例: `suke1117-22`）を
   `data/site.yml` の `amazon.associate_tag` に設定する
   （またはリポジトリの Secrets に `AMAZON_ASSOCIATE_TAG` として登録する）

> `associate_tag` が空のあいだは、リンクはタグなしのAmazon URLとして出力されます。
> 審査前にタグ付きリンクを貼るのは規約上まずいので、**空のまま運用して問題ありません**。

---

## 5. PA-API（商品情報の自動取得）を有効にする ※本登録後

PA-APIのキーは「直近180日で3件以上の売上」がないと発行されません。取得できたら:

1. https://affiliate.amazon.co.jp/assoc_credentials/home でアクセスキーを発行
2. リポジトリの **Settings → Secrets and variables → Actions** に登録

| Secret 名 | 内容 |
| --- | --- |
| `AMAZON_ACCESS_KEY` | PA-API のアクセスキー |
| `AMAZON_SECRET_KEY` | PA-API のシークレットキー |
| `AMAZON_ASSOCIATE_TAG` | トラッキングID（例: `suke1117-22`） |

登録すると、毎日のビルド時に商品名・画像・価格・在庫が自動更新されます。

```bash
# ローカルで試す場合
export AMAZON_ACCESS_KEY=... AMAZON_SECRET_KEY=... AMAZON_ASSOCIATE_TAG=...
python scripts/fetch_products.py --refresh
python scripts/fetch_products.py --search "ワイヤレスイヤホン" --limit 5
```

取得結果は `data/amazon_cache.json` に入り、
**`products.yml` に手で書いた内容は上書きされません**（未記入の項目だけ補完されます）。

### 価格表示について

PA-API の規約では、取得した価格を表示できるのは**24時間以内**です。
そのため `data/site.yml` の `price_display` は既定で `range`（価格帯のみ表示）にしています。
1日1回以上ビルドする運用にした上で `exact` に変えると、実売価格が表示されます。

---

## 6. Xへの自動投稿を有効にする

### キーの取得

1. https://developer.x.com/ でアプリを作成
2. アプリの権限を **Read and Write** にする（Read only だと投稿できません）
3. **API Key / API Secret / Access Token / Access Token Secret** を発行
   - 権限を変更した場合は、Access Token を**再発行**しないと反映されません

### Secrets に登録

| Secret 名 |
| --- |
| `X_API_KEY` |
| `X_API_SECRET` |
| `X_ACCESS_TOKEN` |
| `X_ACCESS_TOKEN_SECRET` |

### 動作確認

Actions タブ →「Xへの自動投稿」→ Run workflow →
`投稿せず文面だけ確認する` を **true** のまま実行して、文面を確認します。
問題なければ **false** で実行すると実際に投稿されます。
以降は `.github/workflows/post-to-x.yml` の cron（1日4回）で自動投稿されます。

### 投稿数の上限

Xの無料プランは**月500投稿**（1日あたり約16件）です。
1日4回なら月120件程度なので余裕があります。増やす場合は cron を追加してください。

### 投稿の中身を変える

`data/x_templates.yml` で調整します。

- `templates` / `roundup_templates` … 文面。同じ商品には毎回違うテンプレートが当たります
- `min_repost_interval_days` … 同じ商品を再投稿するまでの日数（既定7日）
- `link_target` … `site`（自サイト記事へ誘導）か `amazon`（アフィリンク直貼り）
- `ad_label` … 広告表示。**消さないでください**

商品ごとに `no_post: true` を付けると、その商品は投稿対象から外れます。

---

## 7. 運用のリズム

| 頻度 | やること |
| --- | --- |
| 商品を買ったとき | `new_item.py` でレビューを1本足す |
| 週1回 | まとめ記事を1本更新するか新規作成する |
| 自動 | 毎日のビルド、1日4回のX投稿 |

自動化しているのは**配信と更新**の部分だけで、**評価の中身は自分で書く**設計です。
中身まで自動生成すると、Amazonアソシエイトの審査・維持の両方でリスクになります。

---

## トラブルシューティング

| 症状 | 原因と対処 |
| --- | --- |
| Pagesが404 | Settings → Pages の Source が `GitHub Actions` になっているか確認 |
| CSSが当たらない | `data/site.yml` の `base_path` がリポジトリ名と一致しているか確認 |
| 投稿ワークフローが「投稿対象なし」で終わる | 全商品が `min_repost_interval_days` 以内に投稿済み。商品を足すか日数を減らす |
| X API が 403 | アプリ権限が Read only。Read and Write に変更し、Access Token を再発行する |
| 履歴のコミットで失敗する | Settings → Actions → Workflow permissions が `Read and write` か確認 |
| PA-API が 429 | リクエスト過多。売上が少ないとレート制限が厳しいので、更新頻度を下げる |
