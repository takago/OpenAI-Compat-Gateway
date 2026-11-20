# OpenAI-Compat-Gateway

OpenAI互換APIと，Irodori-TTS-Server・Kokoro-FastAPIを1つのURLで利用するセルフホスト型ゲートウェイです．バックエンドの登録，モデルの公開，APIキーの発行をWeb管理画面から行えます．

[English README](README.md)

金沢工業大学 TAKAGO LABによる開発プロジェクトです．OpenAI公式の製品ではありません．

## APIキーとエンドポイント

APIキーには**標準キー**と**拡張キー**の2種類があります．

| | 標準キー | 拡張キー |
|---|---|---|
| 転送先 | リクエストの公開モデルIDで決定 | キーに指定した1つのバックエンド |
| OpenAI標準エンドポイント | 利用可能なモデル・機能の範囲で利用 | 指定バックエンドが対応すれば利用可能 |
| **拡張エンドポイント** | **利用不可** | **利用可能**（上流サーバが対応する場合） |
| 設定する場所 | APIキー → 追加 → 標準キー | APIキー → 追加 → 拡張キー |

**拡張キーを使うと，OpenAI標準APIに加え，指定したバックエンドが提供する拡張エンドポイントにもアクセスできます．** 例えばIrodori-TTSやKokoro-TTSの `GET /v1/audio/voices` は拡張エンドポイントです．拡張キーを使えば，そのキーで指定したサーバからVoice一覧を取得できます．

```text
同じゲートウェイURL（/v1/...）
 ├─ 標準キー → 公開モデルIDで転送先を選択 → OpenAI標準API
 │                         例: POST /v1/audio/speech
 └─ 拡張キー → キーに登録されたバックエンドへ転送
                            例: GET /v1/audio/voices
                            例: POST /v1/audio/voices（Irodori）
                            例: POST /v1/audio/voices/combine（対応するKokoro）
```

拡張キーの対象は**1つのバックエンド**です．Irodori-TTSを2台登録した場合も，それぞれに別の拡張キーを発行できます．拡張エンドポイントの有無や動作は，上流サーバの実装によって異なります．

## インストール

Linux，Python 3，systemdを使用します．

```bash
git clone https://github.com/takago/OpenAI-Compat-Gateway.git
cd OpenAI-Compat-Gateway
sudo ./install.sh
```

`http://127.0.0.1:8765/admin` にアクセスし，管理者パスワード（12文字以上）を設定します．外部公開時はHTTPSを使用してください．例えばCaddyでは次のように設定できます．

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

この場合，クライアントのAPIベースURLは `https://ai.example.com/v1` です．

## 初期設定

1. **バックエンド → 追加**：バックエンドID，接続先URL（末尾 `/v1`），種別（**OpenAI互換／Irodori-TTS／Kokoro-TTS**）を登録します．
2. **モデル**：必要なモデルを自動取得または手動登録し，公開モデルIDと対応エンドポイントを設定します．Irodori・Kokoroもモデル一覧に表示できます．
3. **APIキー → 追加**：標準キーなら利用を許可する機能とモデルを，拡張キーなら対象バックエンドを1つ指定します．
4. **APIキーをコピー**：発行時に表示されるキーをクライアントへ設定します．キーはハッシュ保存のため，全文を後から表示できません．

管理画面の「対応エンドポイント」に黄緑色で表示される拡張エンドポイントは，バックエンド種別から推定した候補です．実サーバで利用できるかはそのサーバの仕様で確認してください．

上流サーバが別途認証を要求する場合は，バックエンド設定にその認証情報を登録します．ゲートウェイがクライアントへ発行するAPIキーとは別のものです．

### APIキーの管理

「APIキー」タブでは，キー名，有効期限，有効・無効，権限，キーの再発行・削除を管理できます．「キー名」は管理用の名前，「キーID」は内部識別番号，「APIキー」は認証に使う秘密文字列です．

「利用状況」タブでは，標準キーと拡張キーのリクエスト件数や応答開始時間を確認できます．

## APIの利用例

以下のURL・キーは設定例です．`$STANDARD_KEY`，`$IRODORI_KEY`，`$KOKORO_KEY` には発行したキーを設定してください．

### 標準キー：OpenAI標準の音声生成

公開モデルIDを `irodori-tts` とした例です．標準キーでは，バックエンド種別に関係なく，公開されている対応モデルを使用できます．

```bash
curl https://ai.example.com/v1/audio/speech \
  -H "Authorization: Bearer $STANDARD_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"irodori-tts","input":"こんにちは","voice":"hadou","response_format":"wav"}' \
  --output speech.wav
```

チャットや画像生成なども，それぞれ公開モデルIDを指定して従来どおり利用できます．

### 拡張キー：Irodori-TTSのVoice一覧・管理

Irodori-TTSバックエンドを選んで発行した拡張キーで，そのサーバの拡張エンドポイントにアクセスします．

```bash
curl https://ai.example.com/v1/audio/voices \
  -H "Authorization: Bearer $IRODORI_KEY"
```

Voiceのアップロード・取得・更新・削除も，Irodori-TTS-Serverが提供するAPI形式で利用できます．同じ拡張キーで `POST /v1/audio/speech` にもアクセスできます．

### 拡張キー：Kokoro-TTSのVoice一覧

Kokoro-TTSバックエンドを選んで発行した拡張キーを使用します．

```bash
curl https://ai.example.com/v1/audio/voices \
  -H "Authorization: Bearer $KOKORO_KEY"
```

接続先のKokoro-FastAPIが対応していれば，`POST /v1/audio/voices/combine` などの拡張エンドポイントも利用できます．Kokoro固有のオプションは上流の実装に従います．

## 更新と運用

更新前にSQLiteデータベース `/var/lib/openai-compat-gateway/gateway.sqlite3` をバックアップし，次のコマンドで更新してください．既存のAPIキーと設定は自動移行の対象です．

```bash
git pull
sudo ./install.sh
sudo systemctl status openai-compat-gateway
sudo journalctl -u openai-compat-gateway -f
```

### セキュリティ上の注意

- **拡張キーは指定バックエンドの `/v1/` 以下に広くアクセスできます．** Voiceの削除など，変更操作も許可されるため，信頼できるクライアントにのみ渡してください．
- 拡張キーによる転送対象は `/v1/` 以下のHTTP APIです．WebSocketには対応しません．
- リクエスト本文は既定で最大256 MiBです．転送前にサイズを確認し，レスポンスはストリーミング処理します．
- 管理画面の外部公開時はHTTPSとアクセス制限を利用してください．上流の認証情報はSQLiteへ保存されますが，保存時の暗号化は行いません．
- 自動テストはモック環境で実行されています．Irodori・Kokoroの実サーバとの動作確認は別途必要です．

## 謝辞

[Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server) の開発者 **Aratakoさん**，および [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) の開発者 **remskyさんとコントリビューターの皆様** に感謝します．

## ライセンス

MIT License．Copyright (c) 2026 TAKAGO LAB．
