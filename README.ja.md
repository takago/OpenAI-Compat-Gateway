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
4. モデルを再検出し，公開対象を選択します．
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

`GET /v1/models` に公開モデルを集約し，モデル名と機能に基づいてリクエストを転送します．チャットのSSEストリーミング，OpenAI SDKの `extra_body`，音声・画像のmultipart形式，動画生成ジョブの状態取得を扱います．新しいモデルは自動公開せず，管理者が許可します．

## ライセンス

MIT License．Copyright (c) 2026 TAKAGO LAB．
