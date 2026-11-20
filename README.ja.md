# OpenAI-Compat-Gateway

OpenAI互換APIと，Irodori-TTS-Server・Kokoro-FastAPIを1つの入口から利用するセルフホスト型ゲートウェイです．**OpenAI互換はモデル名，Irodori-TTSとKokoro-TTSはバックエンド専用APIキー**によって転送先を決定します．

[English README](README.md)

金沢工業大学 TAKAGO LAB による開発プロジェクトです．OpenAI公式の製品ではありません．

## 主な機能

- **モデル管理**：すべてのバックエンドでモデル一覧と対応エンドポイントを表示・編集できます．OpenAI互換APIの転送先は公開モデル名で決まります．
- **Irodori-TTS／Kokoro-TTS**：「APIキー」タブで対象バックエンドを選択して特定バックエンド専用キーを発行し，`/v1/...` 以下のHTTPメソッド・パス・クエリ・本文・レスポンスを原則そのまま中継します．`model` や `voice` は書き換えません．
- 設定はSQLiteに保存し，管理画面から編集できます．発行したAPIキーはハッシュで保存します．
- 標準キーでは専用バックエンドを利用できず，特定バックエンド専用キーで別のバックエンドを操作することもできません．

| バックエンド種別 | キー | 転送先の決定 |
|---|---|---|
| OpenAI互換 | 標準キー | リクエスト内の `model` |
| Irodori-TTS | 「APIキー」タブで特定バックエンド専用キーを発行 | APIキー |
| Kokoro-TTS | 「APIキー」タブで特定バックエンド専用キーを発行 | APIキー |

## インストール

Linux，Python 3，systemdを使用します．

```bash
git clone https://github.com/takago/OpenAI-Compat-Gateway.git
cd OpenAI-Compat-Gateway
sudo ./install.sh
```

`http://127.0.0.1:8765/admin` を開き，12文字以上の管理者パスワードを設定してログインします．外部公開する前に初期設定を完了させてください．外部アクセスにはHTTPSを推奨します．

Caddyの設定例：

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

APIの接続先は `https://ai.example.com/v1`，管理画面は `/admin` です．

## バックエンドの登録

管理画面の「バックエンド」→「追加」で，管理用ID，`/v1` で終わる上流URL，種別を指定します．

- **OpenAI互換**：従来どおり「モデル」で公開設定・対応エンドポイントを管理し，「APIキー」で利用権限を設定します．
- **Irodori-TTS**：Irodori-TTS-ServerのURL（例：`http://192.168.1.20:8088/v1`）を登録します．
- **Kokoro-TTS**：Kokoro-FastAPIのURL（例：`http://192.168.1.21:8880/v1`）を登録します．

Irodori-TTSやKokoro-TTSも「モデル」タブでモデルの手動追加・公開設定・対応エンドポイントを編集できます．`/v1/models` を提供するサーバでは自動取得も可能です．これらのモデル設定は特定バックエンド専用キーによる転送先の決定には使用しません．

上流サーバ自身が認証を要求する場合は，バックエンドのAPIキー欄にその認証情報を入力します．これはクライアント向けのゲートウェイAPIキーとは異なります．

## APIキーの発行・管理

「APIキー」タブで「追加」（発行）を押し，キーの種類を選択します．

- **標準キー**：OpenAI互換バックエンドのエンドポイントとモデルを選択して発行します．「すべてのOpenAI互換モデル」も選択できます．
- **特定バックエンド専用キー**：登録済みのIrodori-TTSまたはKokoro-TTSバックエンドを1つ選択して発行します．追加画面には選択したバックエンドの登録モデル名も表示されます．バックエンドを2台登録した場合も，それぞれのキーを個別に発行できます．

APIキーの有効期限と有効・無効状態は「APIキー」タブで編集でき，キーの再発行・削除も同じ画面で操作できます．**キー全文は発行・再発行時のみ表示**されます．ハッシュ保存のため，後から全文を復元することはできません．

バックエンドの登録や種別変更だけではAPIキーを発行しません．Irodori-TTSとKokoro-TTSの相互変更では既存の特定バックエンド専用キーを維持します．OpenAI互換に変更すると，紐付く特定バックエンド専用キーは失効・削除されます．モデル登録情報は保持されるため，OpenAI互換へ戻した際に再利用できます．

## APIの使い方

### OpenAI互換（標準キー）

公開モデル名を指定して利用します．従来のチャット・画像生成などの操作は変わりません．

```bash
curl https://ai.example.com/v1/chat/completions \
  -H "Authorization: Bearer $REGULAR_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"my-chat-model","messages":[{"role":"user","content":"こんにちは"}]}'
```

OpenAI互換の音声生成は，従来どおり `model` を指定します．OpenAI標準にないVoice一覧・管理APIは標準キーでは提供しません．

### Irodori-TTS（特定バックエンド専用キー）

```bash
curl https://ai.example.com/v1/audio/voices \
  -H "Authorization: Bearer $IRODORI_KEY"

curl https://ai.example.com/v1/audio/speech \
  -H "Authorization: Bearer $IRODORI_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"irodori-tts","input":"こんにちは","voice":"hadou","response_format":"wav"}' \
  --output speech.wav
```

Voiceの追加・取得・更新・削除も，Irodori-TTS-Serverと同じAPI形式で利用できます．

### Kokoro-TTS（特定バックエンド専用キー）

```bash
curl https://ai.example.com/v1/audio/voices \
  -H "Authorization: Bearer $KOKORO_KEY"

curl https://ai.example.com/v1/audio/speech \
  -H "Authorization: Bearer $KOKORO_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","input":"Hello","voice":"af_bella","response_format":"mp3"}' \
  --output speech.mp3
```

利用するKokoro-FastAPIが対応していれば，`/v1/audio/voices/combine` なども同様に転送されます．実際に使用できるAPIやオプションは上流サーバのバージョンに依存します．

**Irodoriを2台登録した場合，それぞれに特定バックエンド専用キーを発行できます．** 音声生成とVoice一覧・管理は，どちらもキーに対応する1台へ転送します．`voice` は通常のリクエスト項目として，そのまま上流へ送ります．

## 更新と運用

既存データベースは起動時に自動移行され，従来のAPIキーはそのまま利用できます．既存バックエンドの種別はすべて「OpenAI互換」になります．設定データベースは `/var/lib/openai-compat-gateway/gateway.sqlite3` です．更新前にバックアップを保存してください．

```bash
sudo systemctl status openai-compat-gateway
sudo journalctl -u openai-compat-gateway -f
sudo systemctl restart openai-compat-gateway
```

`git pull` 後に `sudo ./install.sh` を再実行すると更新できます．管理画面にはSQLiteバックアップ・復元機能があります．バックアップには上流認証情報とAPIキーのハッシュが含まれるため，厳重に保管してください．上流認証情報はアクセス権を制限したSQLiteに保存されますが，保存時の暗号化は行いません．

### セキュリティと制約

- 特定バックエンド専用キーは，そのキーに結び付いた有効なバックエンドの `/v1/` 以下だけを転送します．WebSocketや `/v1/` 以外のパスには対応しません．
- 特定バックエンド専用キーでは `GET`・`POST`・`PUT`・`DELETE` を中継するため，Voice削除などの操作も可能です．上流の `/v1/` 全体にアクセスできる鍵として取り扱ってください．
- リクエスト本文は最大256 MiB（既定値）まで一度バッファに読み込み，レスポンスはストリーミング転送します．すべてのHTTP機能を完全再現するものではありません．
- 自動テストはモックサーバを使用しています．Irodori／Kokoroの実サーバとの結合試験は別途必要です．

## 謝辞

**[Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server)** の開発者 **Aratakoさん**，および **[Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI)** の開発者 **remskyさんとコントリビューターの皆様** に感謝します．両プロジェクトは独立して開発・公開されたオープンソースソフトウェアです．

## ライセンス

MIT License．Copyright (c) 2026 TAKAGO LAB．
