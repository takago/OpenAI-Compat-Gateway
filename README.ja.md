# OpenAI-Compat-Gateway

複数のPCで動作するOpenAI互換APIサーバを，単一の `/v1` エンドポイントから利用するための軽量ゲートウェイです．利用者ごとにAPIキーを発行し，モデルとエンドポイントの利用権限を管理できます．

開発：TAKAGO LAB（Kanazawa Institute of Technology）．OpenAIの公式製品ではありません．

[English README](README.md)

## 主な機能

- 複数のバックエンドを単一のAPI URLに集約
- モデルごとの公開名，公開状態，対応エンドポイントを管理
- APIキーごとに利用可能なモデル・エンドポイントと有効期限を設定
- チャット，Responses，Embeddings，画像，音声，動画などのOpenAI互換APIを転送
- SSE，multipartアップロード，JSONの追加パラメータを転送
- Irodori-TTS-Serverの音声生成・Voice管理APIを転送
- ブラウザの管理画面で設定，利用状況の確認，バックアップ・復元

## インストール

対象はPython 3，venv，pip，Git，systemdを利用できるLinuxです．必要に応じて `python3-venv` をインストールしてください．

```bash
git clone https://github.com/takago/OpenAI-Compat-Gateway.git
cd OpenAI-Compat-Gateway
sudo ./install.sh
```

専用のシステムユーザ `openai-gateway` を作成し，プログラムを `/opt/openai-compat-gateway` に配置します．systemdのシステムサービスとして起動します．`.env`，`config.json`，初期APIキーの事前準備は不要です．

## 初期設定（最初の1回）

1. サーバ自身で `http://127.0.0.1:8765/admin` を開きます．
2. 管理者パスワード（12文字以上）を登録してログインします．
3. **バックエンド**で接続先サーバの `/v1` URLと，必要なら上流APIキーを登録します．
4. **モデル**でモデルを取得するか，手動追加します．モデルの「編集」から公開名・公開状態・対応エンドポイントを設定します．
5. **APIキー**で利用者用キーを発行し，利用可能なモデル・エンドポイント・有効期限を設定します．

**重要：** 最初に `/admin` へアクセスした人が管理者を登録できます．初期設定が終わるまではインターネットへ公開せず，遠隔からの管理にはHTTPSを利用してください．

### 管理画面で知っておきたいこと

- 新しく取得したモデルは，最初は**非公開・エンドポイント未設定**です．両方を設定しないとAPIから使えません．
- 「非表示」は管理画面の一覧から隠すだけで，APIの公開状態とは異なります．
- モデルを「削除」すると登録が消えます．上流の `/v1/models` から取得したモデルは，再取得で再登録できます（非公開・エンドポイント未設定）．手動追加したモデルは手動で再登録してください．
- 上流が `/v1/models` を提供しない場合も，モデルを手動追加できます．
- APIキーの秘密値は発行・再発行時だけ表示されます．

## クライアントから利用する

例えばCaddyで次のように公開します．

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

- API Base URL：`https://ai.example.com/v1`
- 管理画面：`https://ai.example.com/admin`

モデル一覧の例：

```bash
curl -H "Authorization: Bearer $API_KEY" \
  https://ai.example.com/v1/models
```

API呼び出しでは，管理画面で設定した**公開モデル名**を `model` に指定します．ゲートウェイが対応するバックエンドへ転送します．

## Irodori-TTS-Serverを利用する

[Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server)を登録する例です．

1. **バックエンド**：Irodoriの `/v1` URLを登録します（例：`http://192.168.1.20:8088/v1`）．
2. **モデル**：上流モデル `irodori-tts` を取得または手動登録し，公開名（例：`irodori-public`）を設定します．
3. そのモデルで **`POST /v1/audio/speech`** を有効化し，公開します．
4. **APIキー**：利用者にそのモデルと音声生成エンドポイントの権限を付与します．

音声生成例：

```bash
curl -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"irodori-public","input":"こんにちは．","voice":"none","response_format":"wav","irodori":{"caption":"明るく穏やかな声"}}' \
  https://ai.example.com/v1/audio/speech --output speech.wav
```

`voice`，`speed`，`response_format`，`stream_format`，`irodori` オブジェクトは原則そのまま上流へ転送します．バイナリ音声とSSEストリームにも対応します．`irodori.ref_wav` などのファイルパスは**上流のIrodoriサーバ**で解決されます．

### Voice一覧と管理

ゲートウェイは以下のIrodori独自APIも転送します．

| メソッド | API | 用途 |
|---|---|---|
| GET | `/v1/audio/voices` | Voice一覧 |
| POST | `/v1/audio/voices` | Voice登録 |
| GET | `/v1/audio/voices/{voice_id}` | Voice情報 |
| PUT | `/v1/audio/voices/{voice_id}` | Voice更新 |
| DELETE | `/v1/audio/voices/{voice_id}` | Voice削除 |

取得先となる音声バックエンドが複数ある場合は，公開モデル名をクエリパラメータで指定します．1つに決まる場合は省略できます．

```bash
curl -H "Authorization: Bearer $API_KEY" \
  'https://ai.example.com/v1/audio/voices?model=irodori-public'
```

**権限上の注意：** Voiceの読み取り・登録・更新・削除は，同じ `audio_speech` 権限で扱われます．音声生成を許可したキーでもVoice管理APIにアクセスできるため，キーの配布範囲に注意してください．`GET /v1/audio/voices` はOpenAI標準ではなく，Irodori固有の拡張です．

## 管理と更新

```bash
sudo systemctl status openai-compat-gateway
sudo journalctl -u openai-compat-gateway -f
sudo systemctl restart openai-compat-gateway
```

更新時はローカルのGitクローンで `git pull` し，再び `sudo ./install.sh` を実行します．データベースは `/var/lib/openai-compat-gateway/gateway.sqlite3` にあり，再インストールしても維持されます．更新前にバックアップしてください．

「利用状況」タブでは設定のバックアップ・復元ができます．復元時には管理者パスワードとサービスの再起動が必要です．バックアップには上流のAPIキーなどの秘密情報が含まれます．

### セキュリティ

- 遠隔管理にはHTTPSを使用してください．
- バックエンド認証情報はアクセス権を制限したSQLiteに保存されますが，**保存時の暗号化は行いません**．
- HTTPS証明書の検証は有効です．LinuxのCA証明書ファイル `/etc/ssl/certs/ca-certificates.crt` を参照します．
- 各モデルを公開する前に，許可するエンドポイントとAPIキーの権限を確認してください．

## ライセンス

MIT License．Copyright (c) 2026 TAKAGO LAB．
