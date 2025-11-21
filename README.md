# Twitter to Discord Bridge

FastAPIサーバーがローカルのRSSHub経由で指定アカウントの投稿を監視し、Discordのスラッシュコマンドを通してチャンネルに投げるブリッジです。

## 特徴
- RSSHubが提供する `RSSHUB_BASE_URL/twitter/user/<ユーザーID>` を周期的に叩いて投稿を取得
- Discord Botを `/add`/`/remove` で制御し、チャンネル毎に任意のアカウントを監視
- 永続化された `subscriptions.json` に設定を保存し、再起動後も定義を保持
- `include_reposts`/`include_quotes` は本文中の `RT`/`引用` などのキーワードで除外できます

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
- `DEFAULT_POLL_INTERVAL_SECONDS` : `/add` で `polling` を省略した際のデフォルト（60秒）
- `MIN_POLL_INTERVAL_SECONDS` : 指定可能な最小値（60秒）
- `SUBSCRIPTIONS_PATH` : 監視設定を保存するファイルパス（省略時 `subscriptions.json`）
- `GUILD_IDS` : カンマ区切りで指定するスラッシュコマンドを配布する Discord ギルド ID（省略するとグローバルコマンドになります）

## 実行
```bash
python -m uvicorn app.main:app --reload
```

## Docker
```bash
# Build the image
docker build -t x2discord .

# Run with explicit docker command
docker run --env-file .env -p 8000:8000 \
	-v ${PWD}/subscriptions.json:/app/subscriptions.json x2discord

# Or use docker compose
docker compose up -d
```

この Compose は `x2discord` のみを起動するため、RSSHub は別リポジトリ・ホストで稼働させておく必要があります。ホストの `http://localhost:1200` に RSSHub を立ち上げている場合、コンテナ内からアクセスするには `.env` の `RSSHUB_BASE_URL` を `http://host.docker.internal:1200`（Windows/macOS）またはホストIPに設定してください。

## 動作確認
起動後 `GET /health` が `{"status": "ok"}` を返すか確認してください。

## スラッシュコマンド
- `/add account:<アカウント名> polling:<秒> include_reposts:<true|false> include_quotes:<true|false>`
	- `account` : `x.com` のアカウント名（`https://x.com/ユーザー` の形でもOK）
	- `polling` : 監視間隔（秒）。省略した場合、デフォルト値（`DEFAULT_POLL_INTERVAL_SECONDS`）を使用
	- `include_reposts` : リポスト（RT）を含めるか。デフォルト `false`
	- `include_quotes` : 引用ツイートを含めるか。デフォルト `false`
- このコマンドは現在のチャンネルにアカウントの監視設定を追加します（`MIN_POLL_INTERVAL_SECONDS` より短い値は指定できません）
- `/edit account:<アカウント名> polling:<秒> include_reposts:<true|false> include_quotes:<true|false>`
	- 指定したアカウントの設定を上書きします。省略した項目は現状を維持します
- `/remove account:<アカウント名>`
	- そのチャンネルの監視設定を削除します

## サブスクリプション永続化
アプリは `SUBSCRIPTIONS_PATH` で指定された JSON ファイルに設定を保存します。Botを立ち上げ直しても最後の監視リストを引き継ぎます。

## テスト
```bash
pytest
```

## 参考
- Botに `applications.commands` スコープを与えること
- `DISCORD_BOT_TOKEN` と同じアプリのBotをサーバーに招待し、`Send Messages` 権限を付与
