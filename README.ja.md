# OpenAI-Compat-Gateway

複数PC上のOpenAI互換APIを，単一の `/v1` で利用するための軽量ゲートウェイです．TAKAGO LAB（Kanazawa Institute of Technology）が開発しています．OpenAI公式の製品ではありません．

## インストール

Ubuntu等のsystemdを使用するLinux向けです．Python 3の `venv` と `pip`，`git`，`systemd` が必要です．必要に応じて `python3-venv` を先にインストールしてください．

```bash
git clone https://github.com/takago/OpenAI-Compat-Gateway.git
cd OpenAI-Compat-Gateway
sudo ./install.sh
```

既に `/opt/openai-compat-gateway` にcloneしている場合も，そのディレクトリから `sudo ./install.sh` を実行できます．

インストーラは `openai-gateway` システムユーザを作成し，プログラムを `/opt/openai-compat-gateway` に配置して，systemdの**システムサービス**として有効化します．ログイン不能な専用ユーザで実行され，ユーザセッションや `loginctl enable-linger` は必要ありません．

以下の設定ファイルは不要です．

- `.env`
- `config.json`
- 初期APIキー
- 管理者パスワードの事前ハッシュ化
- セッション秘密鍵の事前生成

## 初期設定

1. サービス起動後に `https://＜Caddyで設定したホスト＞/admin` へアクセスします．同じPCからは `http://127.0.0.1:8765/admin` でも開けます．
2. 初回のみ管理者パスワード（12文字以上）を登録します．
3. 管理画面にログインして，バックエンドURLを登録します（例：`http://192.168.1.10:11434/v1`）．APIキーが必要なバックエンドは，管理画面に直接入力できます．
4. `/v1/models` が利用できればモデルを自動取得します．利用できない場合は「モデルを手動追加」から登録し，各モデルで対応するOpenAI互換APIエンドポイントを選択します．
5. 管理画面で利用者向けAPIキーを発行し，エンドポイント・モデルの権限および有効期限を設定します．

**重要な注意**：初回管理者登録はアクセス元の制限を設けていません．登録前に第三者が `/admin` にアクセスすると，先に管理者登録される危険があります．インストール後すみやかに初期登録を行い，それまでは管理ポートをインターネットへ公開しないでください．Caddyで管理画面を公開する場合はHTTPSを使用してください．

## Caddy設定例

同じPCでCaddyを動かす場合：

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

利用者API：`https://ai.example.com/v1`，管理画面：`https://ai.example.com/admin`．

## 運用

```bash
sudo systemctl status openai-compat-gateway
sudo journalctl -u openai-compat-gateway -f
sudo systemctl restart openai-compat-gateway
```

更新はGitHubのクローンを `git pull` したあと，再度 `sudo ./install.sh` を実行します．SQLiteのデータは `/var/lib/openai-compat-gateway/gateway.sqlite3` に保持し，再インストール時も削除しません．事前にデータベースをバックアップしてください．

バックエンドAPIキーは専用ユーザのみが読めるSQLiteに保存されますが，現行版では暗号化保存していません．ホスト管理者とファイルバックアップの管理に注意してください．

## API仕様

`GET /v1/models` に公開モデルを集約し，モデル名と機能に基づいてリクエストを転送します．チャットのSSEストリーミング，OpenAI SDKの `extra_body`，音声・画像のmultipart形式，動画生成ジョブの状態取得を扱います．新しいモデルは自動公開せず，管理者が許可します．対応エンドポイントはモデル単位で手動設定します．`/v1/models` がなくてもモデルを手動登録できます．


## Web管理画面（v0.5.0）

管理画面は「バックエンド」「モデル」「APIキー」「利用状況」の4タブ構成です．**一覧は確認専用，編集は「編集」ボタンから開く**方式に統一しています．変更は「保存」で確定し，「キャンセル」では破棄されます．

- **バックエンド**：登録済みサーバの一覧，URL・キー等の編集，追加・削除．エンドポイントはモデル設定を集約して表示します．
- **モデル**：バックエンドによる絞り込みと名前検索，自動取得・手動追加，モデルごとの公開名・公開状態・対応エンドポイントの編集，非表示・削除．
- **APIキー**：キー発行・再発行・削除，エンドポイントとモデルの権限・有効期限の編集．全文は発行・再発行時だけ確認できます．
- **利用状況**：API利用統計，設定のバックアップ・復元．

一覧で編集欄や多数のチェックボックスを常時表示しないため，モデル数が多い環境でも画面を把握しやすくしています．

## ライセンス

MIT License．Copyright (c) 2026 TAKAGO LAB．

## 管理画面の操作

1. 「バックエンド」からURLと必要に応じてAPIキーを登録します．
2. 「モデル」でモデル一覧を再取得します．`/v1/models`を提供しない場合は「モデルを手動追加」を使用します．
3. 登録されたモデルの「編集」から公開モデルID，公開・非公開，対応エンドポイントを設定します．新規モデルは非公開・エンドポイント未設定です．
4. 「APIキー」からキーを発行し，モデル・エンドポイントの権限と有効期限を設定します．

「非表示」は管理画面の一覧だけに作用し，APIの公開状態は変更しません．「削除」はゲートウェイ内の登録を消します．自動検出モデルは再取得で復活できます．

### Manual model registration / モデルの手動登録

`/v1/models`を提供しないバックエンドでは，バックエンドのモデル自動取得を無効にし，管理画面のモデル管理から手動登録します．バックエンド側モデルID，公開名を設定します．エンドポイントはモデルごとにチェックボックスで選択します．

## HTTPSバックエンドの証明書

`gateway.py` は `/etc/ssl/certs/ca-certificates.crt` を `SSL_CERT_FILE` と `REQUESTS_CA_BUNDLE` に設定します．このパスを持つLinux環境を想定しています．証明書の検証を無効にはしません．

モデル削除：手動登録と自動検出のどちらも管理画面から削除できます．削除した自動検出モデルは，バックエンドの `/v1/models` に存在すれば再取得時に再登録されます（非公開・対応エンドポイント未設定）．手動登録モデルは再取得で復活しないため，必要なら手動登録します．公開名の編集はバックエンド側のモデルIDの変更ではありません．

## データの保存・復元

SQLiteに登録情報とAPIキーのハッシュを保持します．管理画面の「利用状況」タブからSQLiteのバックアップと復元ができます．復元時は管理者パスワードを再入力し，サービスを再起動してください．バックアップにはバックエンドの認証情報が含まれるため，厳重に保管してください．

### 既存データベースのバックアップ（更新前）

```bash
sudo systemctl stop openai-compat-gateway
sudo install -m 600 /var/lib/openai-compat-gateway/gateway.sqlite3 \
  /var/lib/openai-compat-gateway/gateway.sqlite3.pre-v0.4.0
sudo systemctl start openai-compat-gateway
```

**注意**：登録モデルにエンドポイントが1つもない場合，公開状態でもAPI呼び出しの対象にはなりません．モデルのエンドポイント欄を設定してください．

## エンドポイントと再取得

モデル一覧には，設定済みエンドポイントのみを `v1/chat/completions` のようなラベルで表示します．モデルごとの「編集」で公開名・公開状態・エンドポイントをまとめて編集できます．対応能力を推測して自動チェックすることはありません．

削除した自動検出モデルはバックエンドの `/v1/models` に存在すれば再取得で再登録されます．再登録時は非公開・エンドポイント未設定です．手動追加モデルは必要なら再び手動で登録してください．

## コンパクトな管理画面（v0.5.1）

バックエンド，モデル，APIキーの一覧をPC向けの横並び表形式に整理しました．検索・絞り込み・追加操作を同じ行に集約し，「編集」を押した行だけフォームを展開します．狭い画面では表を横スクロールできます．APIとSQLiteの構造は変更していません．


## Kokoro TTS との連携

[remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) をはじめ，`POST /v1/audio/speech` を備えたOpenAI互換のKokoroサーバを利用できます．バックエンドURLとして `http://<サーバ>:8880/v1` などを登録し，上流の `kokoro` モデル（サーバによって異なる場合があります）を検出または手動登録します．モデルの対応エンドポイントを **`POST /v1/audio/speech`** に設定して公開し，APIキーにその公開モデルと `audio_speech` の利用を許可してください．

音声生成時は，公開モデル名だけを上流モデル名へ書き換え，`input`，`voice`，`response_format`，`speed`，`stream`，`allow_voice_tags`，`lang_code`，`volume_multiplier`，`normalization_options`，`voice_aliases`，`ssml` などの値を基本的にそのまま転送します．音声データやHTTPストリーミングも変換しません．各オプションの可否はKokoroサーバの種類・バージョンに依存します．特に，SSEや音声クローニングに自動的に対応するわけではありません．

Kokoro-FastAPIの `GET /v1/audio/voices` は音声一覧をそのまま返します．`?legacy=true` も利用できます．さらに `POST /v1/audio/voices/combine` により，音声名のJSON文字列または文字列配列を送って，合成した音声の `.pt` ファイルを受け取れます．ただし，上流サーバの `ALLOW_LOCAL_VOICE_SAVING=true` が必要であり，無効な場合は上流の403をそのまま返します．ほかのKokoro実装では音声一覧やcombineが存在しないことがあります．

IrodoriとKokoroなど複数の音声バックエンドを利用できるAPIキーでは，音声一覧とcombineのURLへ `?model=<公開モデルID>` を付けて対象を選びます．この選択値は上流へ転送しません．どちらも `audio_speech` 権限を使います．上流が音声の変更操作を公開している場合，APIキーの配布範囲に注意してください．

```bash
# 音声生成
curl -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"kokoro-public","input":"こんにちは","voice":"jf_alpha","response_format":"wav","speed":1.0}' \
  https://gateway.example/v1/audio/speech --output kokoro.wav

# 音声一覧（Kokoro-FastAPI）
curl -H "Authorization: Bearer $API_KEY" \
  'https://gateway.example/v1/audio/voices?model=kokoro-public'

# 音声の合成（上流側で保存機能が有効な場合）
curl -X POST -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '["af_heart","af_bella"]' \
  'https://gateway.example/v1/audio/voices/combine?model=kokoro-public' --output mixed.pt
```

## Irodori-TTS-Server との連携

[Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server) を音声合成バックエンドとして接続できます．管理画面でバックエンドURLを `http://<サーバ>:8088/v1` として登録し，`/v1/models` から `irodori-tts` を検出したら，モデルの対応エンドポイントに **`POST /v1/audio/speech`** を設定し，公開してください．自動検出が利用できない場合は同じモデルIDを手動登録できます．APIキーの許可モデルに公開IDを指定します．

`POST /v1/audio/speech` は `input`，`voice`，`response_format`，`speed`，`stream_format`，`irodori` オブジェクトの内容を基本的に変更せずに転送します．公開モデル名だけ上流モデル名に書き換えます．WAV等の音声バイト列，SSEの `audio_chunk` / `done` イベントもストリーミングで中継します．`irodori.caption`，参照音声指定，推論設定，LoRA設定などは上流サーバが解釈します．

音声管理については，下記も中継します（ゲートウェイでは `audio_speech` の権限を使用します）．

| メソッド | パス | 操作 |
|---|---|---|
| GET | `/v1/audio/voices` | 一覧 |
| POST | `/v1/audio/voices` | 音声ファイル登録（multipart） |
| GET | `/v1/audio/voices/{voice_id}` | 音声情報 |
| PUT | `/v1/audio/voices/{voice_id}` | 音声ファイル更新（multipart） |
| DELETE | `/v1/audio/voices/{voice_id}` | 音声削除 |

音声管理エンドポイントにはリクエストの `model` フィールドがないため，アクセス可能な音声バックエンドが**複数**あるときは，クエリパラメータ `?model=<公開モデルID>` で対象を選択してください．このパラメータは上流へ転送しません．同じバックエンドに複数の公開モデルがあるだけなら自動選択できます．音声管理では読み取りと書き込みが同じ `audio_speech` 権限に含まれます．APIキーを配布する際には，音声ファイルを追加・更新・削除できる点に注意してください．

```bash
# 音声一覧
curl -H "Authorization: Bearer $API_KEY" \
  'https://gateway.example/v1/audio/voices?model=irodori-public'

# 音声を登録
curl -X POST -H "Authorization: Bearer $API_KEY" \
  -F 'voice_id=sample' -F 'file=@sample.wav' \
  'https://gateway.example/v1/audio/voices?model=irodori-public'

# Voice Design による音声生成
curl -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"irodori-public","input":"こんにちは．","voice":"none","response_format":"wav","irodori":{"caption":"明るく穏やかな声"}}' \
  https://gateway.example/v1/audio/speech --output speech.wav
```

参照音声のパス（`irodori.ref_wav` など）は**Irodoriサーバ側**で解決されます．ゲートウェイ端末上のパスや任意のHTTP URLは参照できません．
