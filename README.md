# Twitter to Discord Bridge

FastAPIサーバーがローカルのRSSHub経由で指定アカウントの投稿を監視し、Discordのスラッシュコマンドを通してチャンネルに投げるブリッジです。

## 特徴
- RSSHubが提供する `RSSHUB_BASE_URL/twitter/user/<ユーザーID>` を周期的に叩いて投稿を取得
- Discord Botを `/add`/`/remove` で制御し、チャンネル毎に任意のアカウントを監視
- 永続化された `subscriptions.json` に設定を保存し、再起動後も定義を保持
- **Redisを使用した送信済みリンクの永続化** - アプリ再起動後も重複投稿を防止
- `include_reposts`/`include_quotes` は本文中の `RT`/`引用` などのキーワードで除外できます
- `include_keywords`/`exclude_keywords` で任意の文字列を含む/含まない投稿だけ通知できます

## RSSHubの準備
1. `DIYgod/RSSHub` をクローンまたは既存環境を流用し、`docker compose up -d` などで `RSSHub` を起動します。ローカルで `http://localhost:1200` へアクセスできるようにしてください。
2. RSSHubの `.env` で `TWITTER_USERNAME`/`TWITTER_PASSWORD`/`TWITTER_AUTH_TOKEN` などの認証情報を設定し、`/twitter/user/:id` ルートが動作することを確認してください（公式ドキュメント: https://docs.rsshub.app/）。
3. 必要であれば `RSSHUB_BASE_URL` を `http://localhost:1200` 以外に向けます。

## セットアップ
1. Python 3.11以上を準備
2. `.env.example` をコピーして `.env` を編集
3. 依存をインストール
```bash
pip install -r requirements.txt
```

## 環境変数
- `DISCORD_BOT_TOKEN` : Botのトークン（必須）
- `RSSHUB_BASE_URL` : RSSHubのベースURL（デフォルト `http://localhost:1200`）
- `REDIS_URL` : Redis接続URL（デフォルト `redis://localhost:6379/0`、送信済みリンクの永続化に使用）
- `DEFAULT_POLL_INTERVAL_SECONDS` : `/add` で `polling` を省略した際のデフォルト（60秒）
- `MIN_POLL_INTERVAL_SECONDS` : 指定可能な最小値（60秒）
- `SUBSCRIPTIONS_PATH` : 監視設定を保存するファイルパス（省略時 `subscriptions.json`）
- `GUILD_IDS` : カンマ区切りで指定するスラッシュコマンドを配布する Discord ギルド ID（省略するとグローバルコマンドになります）

## 実行
```bash
python -m uvicorn app.main:app --reload
```

## Docker
⚠️ **重要**: X2Discordは**Redisが必須**です。送信済みリンクの永続化に使用し、重複投稿を防止します。

### Docker Composeで起動（推奨）
```bash
# .env.exampleをコピーして設定
cp .env.example .env
# .envを編集してDISCORD_BOT_TOKENなどを設定

# 全サービスを起動（X2Discord、RSSHub、Redis、Browserless）
docker compose up -d
```

`.env`の`RSSHUB_BASE_URL`と`REDIS_URL`はDocker Compose用にデフォルトで設定されています：
- `RSSHUB_BASE_URL=http://rsshub:1200`（Docker内部のサービス名）
- `REDIS_URL=redis://redis:6379/0`（Docker内部のサービス名）

### 単体で起動する場合
```bash
# Build the image
docker build -t x2discord .

# 別途Redisを起動しておく必要があります
docker run -d --name redis -p 6379:6379 redis:alpine

# .envでREDIS_URLとRSSHUB_BASE_URLをホスト接続用に変更
# REDIS_URL=redis://host.docker.internal:6379/0
# RSSHUB_BASE_URL=http://host.docker.internal:1200

# X2Discordを起動
docker run --env-file .env -p 8000:8000 \
	-v ${PWD}/subscriptions.json:/app/subscriptions.json x2discord
```


## 動作確認
起動後 `GET /health` が `{"status": "ok"}` を返すか確認してください。

## スラッシュコマンド
- `/add account:<アカウント名> polling:<秒> include_reposts:<true|false> include_quotes:<true|false> include_keywords:<カンマ区切り> exclude_keywords:<カンマ区切り>`
	- `include_keywords` : 指定したキーワードを含む投稿のみ通知（省略していれば制限なし）
	- `exclude_keywords` : 指定したキーワードを含む投稿は通知しない（除外は許可より優先）
	- `account` : `x.com` のアカウント名（`https://x.com/ユーザー` の形でもOK）
	- `polling` : 監視間隔（秒）。省略した場合、デフォルト値（`DEFAULT_POLL_INTERVAL_SECONDS`）を使用
	- `include_reposts` : リポスト（RT）を含めるか。デフォルト `false`
	- `include_quotes` : 引用・引用リツイートを含めるか。デフォルト `false`
- このコマンドは現在のチャンネルにアカウントの監視設定を追加します（`MIN_POLL_INTERVAL_SECONDS` より短い値は指定できません）
- `/edit account:<アカウント名> polling:<秒> include_reposts:<true|false> include_quotes:<true|false> include_keywords:<カンマ区切り> exclude_keywords:<カンマ区切り>`
	- 指定したアカウントの設定を上書きします。省略した項目は現状を維持します
- `/remove account:<アカウント名>`
	- そのチャンネルの監視設定を削除します
  既存の `subscriptions.json` に `include_keywords`/`exclude_keywords` 欄がない場合でも空リストとして扱い、引き続き全投稿を許可するのでインポート不要です。

## キーワードフィルタの例
`/add` や `/edit` でキーワードを指定する際はカンマまたは改行で複数指定できます。
```
/add account:coolexample polling:60 include_keywords:新作,アップデート exclude_keywords:ネタバレ,広告
```
上記は「新作」または「アップデート」を含む投稿だけ通知し、「ネタバレ」や「広告」を含んでいたら除外します。除外キーワードは許可キーワードより優先されるので、両方にマッチしていても通知されません。

## 既存サブスクリプションの JSON 例
`subscriptions.json` に手動でキーワードを追加するには、各エントリに `include_keywords`/`exclude_keywords` 配列を追加します。既存のエントリに配列がない場合は空配列扱いです。
```json
{
  "subscriptions": {
    "123456": [
      {
        "account": "genshin_official",
        "interval_seconds": 60,
        "include_reposts": false,
        "include_quotes": false,
        "include_keywords": ["新作", "Luna"],
        "exclude_keywords": ["ネタバレ"]
      }
    ]
  }
}
```

## データ永続化
### 設定の永続化
アプリは `SUBSCRIPTIONS_PATH` で指定された JSON ファイルに**設定**を保存します（アカウント、間隔、フィルターなど）。

### 状態の永続化
実行時の**状態**（最後に処理したツイートID、送信済みリンク）は**Redisに保存**されます：
- `last_tweet_id`: 各アカウントの最後に処理したツイートID
- `sent_links`: 各チャンネルで送信済みのリンク履歴（最大1000件）

既存の `subscriptions.json` に `last_tweet_id` が含まれている場合、初回起動時に自動的にRedisに移行されます。

## テスト
```bash
pytest
```

## 参考
- Botに `applications.commands` スコープを与えること
- `DISCORD_BOT_TOKEN` と同じアプリのBotをサーバーに招待し、`Send Messages` 権限を付与
