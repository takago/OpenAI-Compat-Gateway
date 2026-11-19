# OpenAI-Compat-Gateway

このソフトウェアはOpenAIとは無関係の独立プロジェクトです．複数のPC上のOpenAI互換APIを単一の `/v1` に統合する軽量な非公式ゲートウェイです．LLM（複数Ollama，vLLMなど），音声認識・音声合成，画像生成・編集，動画生成を対象とします．

**v0.2.2：APIキーごとの有効期限（無期限・7日・30日・90日・日時指定）と，管理画面からの期限更新に対応しています．**

## 機能

- 単一の `/v1/models` に公開モデルを集約．APIキーごとに見えるモデルを制限
- `/v1/chat/completions` などを `model` に基づいてバックエンドへ転送，SSEストリーミングにも対応
- `/v1/audio/*`，`/v1/images/*`，`/v1/videos*` を転送．音声・画像のmultipart入力にも対応
- 動画ジョブIDと発行キーの関連付けをSQLiteに保存し，再起動後も状態確認・取得を元のサーバへ転送
- 管理画面 `/admin` でバックエンド登録・変更，モデル検出，公開名編集，公開可否を設定
- 利用者向けAPIキーを発行・無効化し，機能名と公開モデルIDでアクセスを制限．無期限・7日・30日・90日・日時指定の有効期限にも対応
- API呼出回数，応答開始までの時間，HTTPステータスの統計データを保存
- 管理者パスワード，署名付きCookie，CSRF防御，独立した管理者認証

**設計上の制約：** `model` を指定せずに複数の公開モデルが候補になる場合はHTTP 400になります．同名モデルは別の公開IDにして区別します．同一モデルを自動負荷分散する機能はありません．`/v1/models` はモデルIDを検出しますが，API種別はバックエンド登録時に手動指定してください．

## インストール

Python 3.10以上を想定します．

```bash
unzip OpenAI-Compat-Gateway-v0.2.2.zip
cd OpenAI-Compat-Gateway
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
cp .env.example .env
chmod 600 .env
```

初回は `config.json` の `backends` にあるサンプルIPアドレスを，研究室の実際の接続先に変更します．`base_url` は必ず `/v1` まで記述します．バックエンドのAPIキーが必要なら `api_key_env` に環境変数名を設定し，その環境変数を `.env` に追加します．

管理者パスワードのハッシュとセッション用秘密鍵を生成します．

```bash
.venv/bin/python setup_admin.py
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

出力された `ADMIN_PASSWORD_HASH` と `ADMIN_SESSION_SECRET` を `.env` に設定します．また，`GATEWAY_API_KEY` を長いランダム値に変更します．この初期キーは初回DB作成時に全権限のAPIキーとして取り込まれます．新しい制限付きキーを作った後，管理画面でこの初期キーを無効化できます．

`config.json` は初回のバックエンド登録用です．初回起動後の管理情報はSQLiteに保存し，以後は管理画面から変更します．

`.env`を読み込んで起動します．

```bash
set -a
source .env
set +a
.venv/bin/uvicorn gateway:app --host 127.0.0.1 --port 8765
```

ブラウザで `http://127.0.0.1:8765/admin` にアクセスします．ただし，初期設定ではCookieにSecure属性が付いているため，通常のHTTPではログインセッションを維持できません．**通常運用はHTTPSのCaddy経由で接続してください．** ローカルHTTPだけでテストする場合に限って `.env` の `ADMIN_COOKIE_SECURE=false` を設定します．

## Caddyとファイアウォール

`Caddyfile.example` を参照してください．標準設定では公開APIを `https://ai.example.com/v1` に集約し，`/admin` は信頼できるネットワークまたはVPNからのみ許可します．

**重要：** Caddyの `remote_ip` はアクセス元IPの制限です．サンプルのCIDRを研究室のネットワーク構成に合わせて置き換えてください．管理者Web画面は学外に直接公開しないでください．Caddyとゲートウェイが別ホストなら，接続先やファイアウォール設定を変更してください．

各AIサーバはゲートウェイから到達できる必要があります．**各GPU PCのAPIポートをインターネットへ開放する必要はありません．** ゲートウェイホストからのアクセスだけを許可することを推奨します．

## 管理画面 `/admin`

1. バックエンドのID，URL，機能，必要ならAPIキー環境変数を登録します．
2. 「モデルを再検出」を押して各サーバの `/v1/models` を取得します．最初に発見したモデルは非公開です．
3. 公開するモデルを選び，必要なら公開IDを変更し，「保存」を押します．
4. APIキーを作成します．機能一覧（例 `chat,embeddings`），モデル一覧（例 `qwen-small,whisper-small`）を指定します．`*` はすべて許可します．
5. **発行されたAPIキーはその場でコピーしてください．後から元の値は表示できません．**

管理操作は再起動なしでSQLiteへ即時反映されます．ただし，**バックエンドキーは環境変数から読み込むため，キーを環境ファイルで変更した場合はsystemdサービスの再起動が必要です．** モデルの自動再検出には一定のキャッシュ時間があります．

サービス権限の内部名：`chat`，`completions`，`responses`，`embeddings`，`audio_transcriptions`，`audio_translations`，`audio_speech`，`images_generations`，`images_edits`，`images_variations`，`videos`．

例：学生キーに `chat,completions,embeddings,audio_transcriptions,audio_speech` を許可し，モデルは `qwen-small,whisper-small,tts-small` だけを許可します．**APIキーごとの許可はモデル一覧だけでなく，転送処理でも確認されます．**

## `extra_body` と独自パラメータ

**対応しています．** OpenAI Python SDKの `extra_body` はJSONリクエストのトップレベルへ統合され，ゲートウェイは `model` 以外の項目を解釈・削除せずに転送します．モデルに公開エイリアスが設定されている場合も，`model` だけをバックエンドのIDに置き換えます．

```python
from openai import OpenAI

client = OpenAI(base_url="https://ai.example.com/v1", api_key="YOUR_GATEWAY_KEY")
response = client.chat.completions.create(
    model="qwen-local",
    messages=[{"role": "user", "content": "こんにちは"}],
    extra_body={"think": False, "options": {"num_ctx": 32768}},
)
```

この場合，バックエンドに送られるJSONには `think` や `options` が含まれます．ネストした `extra_body` オブジェクトを別のフィールドとして送る仕様ではありません．画像・音声APIのmultipartフォームに含まれる追加フィールドもそのまま転送します．各パラメータが実際に機能するかはバックエンドの対応状況によります．認証ヘッダはゲートウェイで差し替えます．

## systemdユーザサービス

`~/OpenAI-Compat-Gateway` に設置する例です．

```bash
mkdir -p ~/.config/systemd/user
cp openai-compat-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now openai-compat-gateway.service
systemctl --user status openai-compat-gateway.service
journalctl --user -u openai-compat-gateway.service -f
```

ログアウト後も起動を継続する場合：

```bash
sudo loginctl enable-linger "$USER"
```

## 動作確認

```bash
curl -H 'Authorization: Bearer YOUR_GATEWAY_KEY' https://ai.example.com/v1/models
curl -X POST https://ai.example.com/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_GATEWAY_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"model":"YOUR_PUBLISHED_MODEL","messages":[{"role":"user","content":"Hello"}]}'
```

## 制限・運用上の注意

- HTTPリクエスト本文をゲートウェイ内でメモリに保持します．巨大な動画のアップロードには不向きです．`max_request_mb`で上限を設定してください．
- 管理画面ではバックエンドのHTTP URLを登録できます．管理者アカウントは信頼できる人物だけに提供してください（内部ネットワークへのSSRFリスク）．
- バックエンドの `/v1/models` が不完全な場合は，管理対象モデルが取得できません．既存設定の `models` に固定モデルを記載し，`discover_models=false` として初回取り込みする方法もあります．
- 非同期動画生成の完了判定，ジョブ取り消し，リミックス動作は各バックエンドの互換性に依存します．
- 利用統計の応答時間は**ストリーミング終了までの時間ではなく，HTTP応答開始までの時間**です．GPUメトリクスやキュー長は含みません．
- 認証用の管理者セッションはSQLiteに保存し，ログアウト時に失効させます．ユーザ向けキーはSHA-256ハッシュで保存されます．管理者パスワードはPBKDF2-SHA256ハッシュで設定します．
- SQLiteファイルは秘密情報に準じてアクセス制限し，定期的にバックアップしてください．構成は単一ワーカーでの運用を想定しています．
- APIキーごとの呼出回数に上限を設けるレートリミットや，詳細GPU監視は未実装です．

## テスト

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```


## APIキーの有効期限

管理画面の「APIキー / 権限」から新規発行時に「無期限」「7日」「30日」「90日」「日付指定（日本時間）」を選べます．日付指定は日本時間の年月日時分で入力します．相対日数は発行した時点から数えます．

既存キーは一覧の「期限」欄を編集して「更新」で期限の設定・延長・無期限化ができます．空欄は無期限です．期限切れキーは削除されず「期限切れ」と表示されます．期限を延長すれば再利用できます．

期限はSQLiteの `api_keys.expires_at` にUTCのUnix秒として保存します．期限時刻に到達するとすべての `/v1/` API（モデル一覧，SSE，動画ジョブ操作を含む）で `401 Unauthorized` となります．既存DBでは起動時に必要な列が自動追加され，既存キーは無期限のまま維持されます．管理画面の認証はAPIキーとは独立しています．

安全上，APIキーの値は作成直後にのみ表示されます．期限付きキーであっても秘密情報として扱ってください．
