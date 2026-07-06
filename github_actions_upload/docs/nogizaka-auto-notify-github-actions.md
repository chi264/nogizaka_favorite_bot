# 乃木坂46 自動通知 GitHub Actions版

この方式は、PCで `bot.py` を起動し続けません。
GitHub Actionsが30分ごとに公式サイトなどを確認し、新着があればDiscord Webhookへ通知します。

## できること

- 乃木坂46公式ブログの新着確認
- 乃木坂46公式ニュースの新着確認
- 乃木坂46公式YouTubeの新着確認
- 乃木坂46公式スケジュールの新着確認
- 推しメン名が含まれる情報を推しメン通知として送信
- 通知済みURLを `data/nogizaka_seen.json` に保存して重複通知を防止

## できないこと

- `/favorite add` のようなDiscordスラッシュコマンド
- Discordからその場でBotに返答させること
- GitHub Actionsの実行時刻を秒単位で正確に保証すること

## 追加したファイル

```text
.github/workflows/nogizaka-auto-notify.yml
scripts/nogizaka_notify.py
requirements-nogizaka-notify.txt
config/favorites.json
data/nogizaka_seen.json
docs/nogizaka-auto-notify-github-actions.md
```

## 1. Discord Webhookを作る

1. Discordで通知したいチャンネルを開く
2. チャンネル名の横の歯車を開く
3. `連携サービス` または `インテグレーション` を開く
4. `ウェブフック` を開く
5. `新しいウェブフック` を作る
6. `ウェブフックURLをコピー` する

## 2. GitHub SecretsにWebhook URLを保存する

GitHubの対象リポジトリで操作します。

1. `Settings` を開く
2. 左メニューの `Secrets and variables`
3. `Actions`
4. `New repository secret`
5. Name に次を入れる

```text
NOGIZAKA_DISCORD_WEBHOOK_URL
```

6. Secret にDiscord Webhook URLを貼り付ける
7. `Add secret`

まずはこの1つだけでOKです。

## 3. 推しメンを設定する

`config/favorites.json` を編集します。

```json
{
  "members": [
    "白石麻衣",
    "鈴木佑捺"
  ],
  "aliases": {
    "白石": "白石麻衣",
    "佑捺": "鈴木佑捺"
  }
}
```

`members` に推しメンの正式名を書きます。
`aliases` は略称です。
例えば `白石` と書いてあるニュースも `白石麻衣` として扱いたい場合に使います。

## 4. GitHubにpushする

追加したファイルをGitHubにpushします。
push後、GitHubの `Actions` タブに `Nogizaka Auto Notify` が出ます。

## 5. 手動でテスト実行する

1. GitHubの `Actions`
2. 左側から `Nogizaka Auto Notify`
3. `Run workflow`
4. 緑の `Run workflow`

初回は古い情報を大量通知しないように、見つかったURLを通知済みとして登録します。
そのため、最初は通知が少ない、またはログ通知だけになることがあります。

次回以降、新しいURLが見つかったときに通知します。

## 6. チャンネルを分けたい場合

必要なら、GitHub Secretsを追加します。
指定しない場合は `NOGIZAKA_DISCORD_WEBHOOK_URL` に送られます。

```text
NOGIZAKA_FAVORITE_WEBHOOK_URL
NOGIZAKA_BLOG_WEBHOOK_URL
NOGIZAKA_NEWS_WEBHOOK_URL
NOGIZAKA_YOUTUBE_WEBHOOK_URL
NOGIZAKA_SCHEDULE_WEBHOOK_URL
NOGIZAKA_LOG_WEBHOOK_URL
```

## 注意点

- GitHub Actionsの定期実行は遅れることがあります。
- GitHub側の混雑などでスキップされることもあります。
- 公式サイトのHTML構造が変わると、ブログ・ニュース・スケジュール取得は修正が必要です。
- YouTubeはRSSを使っているので比較的安定しています。
