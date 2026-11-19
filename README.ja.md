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
4. API機能をチェックボックスで選択します．`/v1/models` が利用できればモデルを自動取得します．利用できない場合は管理画面の「モデルを手動追加」からバックエンド・モデルID・公開名を指定します．
5. 管理画面で利用者向けAPIキーを発行し，機能・モデルの権限および有効期限を設定します．

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

`GET /v1/models` に公開モデルを集約し，モデル名と機能に基づいてリクエストを転送します．チャットのSSEストリーミング，OpenAI SDKの `extra_body`，音声・画像のmultipart形式，動画生成ジョブの状態取得を扱います．新しいモデルは自動公開せず，管理者が許可します．API機能は手動設定のみです．`/v1/models` がなくてもモデルを手動登録できます．

## ライセンス

MIT License．Copyright (c) 2026 TAKAGO LAB．

## 管理画面の操作（v0.3.1）

- バックエンド登録時は，機能をチェックボックスで選択します．登録直後に `/v1/models` を取得し，取得数または失敗理由を画面に表示します．サーバによってはモデル一覧の形式が異なるため，OpenAI互換の `{ "data": [{ "id": "..." }] }` を返す必要があります．URLは `/v1` までを指定してください．
- 「全サーバのモデルを再取得」で結果を確認できます．取得したモデルは自動的には公開されません．モデル一覧で公開チェックを入れて「公開設定をまとめて保存」します．
- APIキーは新規発行または再発行時に表示され，コピーできます．旧キーは再発行時点で失効します．既存キーはハッシュ保存しているため，全文の再表示はできません．キーの削除，権限変更，有効期限変更にも対応します．
- APIキーの「削除」は復元できません．運用中のキーは必要に応じて無効化するか，再発行してください．

**更新**：既存のSQLiteは保持されます．更新前に `/var/lib/openai-compat-gateway/gateway.sqlite3` をバックアップし，GitHubへ改修ファイルを反映後，従来どおり `sudo ./install.sh` で再インストールしてください．



### Manual model registration / モデルの手動登録

`/v1/models`を提供しないバックエンドでは，バックエンドのモデル自動取得を無効にし，管理画面のモデル管理から手動登録します．バックエンド側モデルID，公開名を設定します．API機能は常にチェックボックスで手動選択します．

## HTTPSバックエンドの証明書

`gateway.py` は `/etc/ssl/certs/ca-certificates.crt` を `SSL_CERT_FILE` と `REQUESTS_CA_BUNDLE` に設定します．このパスを持つLinux環境を想定しています．証明書の検証を無効にはしません．
