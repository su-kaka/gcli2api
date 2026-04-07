# GeminiCLI to API

**GeminiCLIおよびAntigravityをOpenAI、GEMINI、Claude API互換インターフェースに変換**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: CNC-1.0](https://img.shields.io/badge/License-CNC--1.0-red.svg)](../LICENSE)
[![Docker](https://img.shields.io/badge/docker-available-blue.svg)](https://github.com/su-kaka/gcli2api/pkgs/container/gcli2api)

[中文](../README.md) | [English](README_EN.md) | 日本語

## 🚀 クイックデプロイ

[![Deploy on Zeabur](https://zeabur.com/button.svg)](https://zeabur.com/templates/97VMEF?referralCode=sukaka)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/su-kaka/gcli2api)
---

## ⚠️ ライセンスについて

**本プロジェクトはCooperative Non-Commercial License (CNC-1.0) の下でライセンスされています**

これは厳格な非商用オープンソースライセンスです。詳細は [LICENSE](../LICENSE) ファイルをご参照ください。

### ✅ 許可される用途:
- 個人の学習、研究、教育目的
- 非営利団体での利用
- オープンソースプロジェクトへの統合（同一ライセンスの遵守が必要）
- 学術研究および論文発表

### ❌ 禁止される用途:
- あらゆる形態の商用利用
- 年間売上が100万ドルを超える企業での利用
- ベンチャーキャピタルの出資を受けた企業または上場企業
- 有料サービスまたは製品の提供
- 商業的な競合利用

## コア機能

### 🔄 APIエンドポイントとフォーマット対応

**マルチエンドポイント・マルチフォーマット対応**
- **OpenAI互換エンドポイント**: `/v1/chat/completions` および `/v1/models`
  - 標準OpenAIフォーマット（messages構造）に対応
  - Geminiネイティブフォーマット（contents構造）に対応
  - フォーマットの自動検出・変換、手動切替不要
  - マルチモーダル入力に対応（テキスト＋画像）
- **Geminiネイティブエンドポイント**: `/v1/models/{model}:generateContent` および `streamGenerateContent`
  - Geminiネイティブ API仕様に完全対応
  - 複数の認証方式: Bearer Token、x-goog-api-keyヘッダー、URLパラメータkey
- **Claudeフォーマット互換**: Claude APIフォーマットに完全対応
  - エンドポイント: `/v1/messages`（Claude API仕様に準拠）
  - Claude標準messagesフォーマットに対応
  - systemパラメータおよびClaude固有機能に対応
  - バックエンド対応フォーマットへの自動変換
- **Antigravity API対応**: OpenAI、Gemini、Claudeフォーマットに対応
  - OpenAIフォーマットエンドポイント: `/antigravity/v1/chat/completions`
  - Geminiフォーマットエンドポイント: `/antigravity/v1/models/{model}:generateContent` および `streamGenerateContent`
  - Claudeフォーマットエンドポイント: `/antigravity/v1/messages`
  - 全Antigravityモデルに対応（Claude、Geminiなど）
  - モデル名の自動マッピングおよびThinkingモード検出

### 🔐 認証とセキュリティ管理

**柔軟なパスワード管理**
- **個別パスワード対応**: APIパスワード（チャットエンドポイント）とコントロールパネルパスワードを個別に設定可能
- **複数の認証方式**: Authorization Bearer、x-goog-api-keyヘッダー、URLパラメータなどに対応
- **JWTトークン認証**: コントロールパネルはJWTトークン認証に対応
- **ユーザーメール取得**: Googleアカウントのメールアドレスを自動取得・表示

### 📊 インテリジェントなクレデンシャル管理システム

**高度なクレデンシャル管理**
- 複数のGoogle OAuthクレデンシャルの自動ローテーション
- 冗長認証による安定性の向上
- ロードバランシングと同時リクエスト対応
- 自動障害検出とクレデンシャル無効化
- クレデンシャル使用統計とクォータ管理
- クレデンシャルファイルの手動有効化/無効化に対応
- クレデンシャルファイルの一括操作（有効化、無効化、削除）

**クレデンシャルステータス監視**
- リアルタイムのクレデンシャルヘルスチェック
- エラーコードの追跡（429、403、500など）
- 自動BAN機能（設定可能）

### 🌊 ストリーミングとレスポンス処理

**複数のストリーミング対応**
- リアルタイムストリーミングレスポンス
- 疑似ストリーミングモード（互換性向上用）
- ストリーミング途切れ防止機能（回答の途切れを防止）
- 非同期タスク管理とタイムアウト処理

**レスポンス最適化**
- 思考チェーン内容の分離
- 推論プロセス（reasoning_content）の処理
- マルチターン会話のコンテキスト管理
- 互換モード（systemメッセージをuserメッセージに変換）

### 🎛️ Web管理コンソール

**フル機能のWebインターフェース**
- OAuth認証フロー管理（GCLIおよびAntigravityデュアルモード対応）
- クレデンシャルファイルのアップロード、ダウンロード、管理
- リアルタイムログ表示（WebSocket）
- システム設定管理
- 使用統計と監視ダッシュボード
- モバイル対応インターフェース

**一括操作対応**
- ZIPファイルによるクレデンシャル一括アップロード（GCLIおよびAntigravity）
- クレデンシャルの一括有効化/無効化/削除
- ユーザーメールの一括取得
- 設定の一括管理
- 全クレデンシャルタイプ統合一括アップロードインターフェース

### 📈 使用状況モニタリング

**リアルタイム監視**
- WebSocketリアルタイムログストリーム
- システムステータス監視
- クレデンシャルヘルスステータス

### 🔧 高度な設定とカスタマイズ

**ネットワークとプロキシ設定**
- HTTP/HTTPSプロキシ対応
- プロキシエンドポイント設定（OAuth、Google APIs、メタデータサービス）
- タイムアウトとリトライ設定
- ネットワークエラー処理とリカバリ

**パフォーマンスと安定性の設定**
- 429エラーの自動リトライ（間隔と回数を設定可能）
- 途切れ防止の最大リトライ回数

**ログとデバッグ**
- マルチレベルログシステム（DEBUG、INFO、WARNING、ERROR）
- ログファイル管理
- リアルタイムログストリーム
- ログのダウンロードとクリア

### 🔄 環境変数と設定管理

**柔軟な設定方法**
- 環境変数による設定
- ホット設定更新（一部設定項目）
- 設定ロック（環境変数優先）

## 対応モデル

全モデルが100万トークンのコンテキストウィンドウに対応。各クレデンシャルファイルで1000リクエストのクォータを提供。

### 🤖 基本モデル
- `gemini-2.5-pro`
- `gemini-3-pro-preview`
- `gemini-3.1-pro-preview`

### 🧠 Thinkingモデル
- `gemini-2.5-pro-high`: Thinkingモード
- `gemini-2.5-pro-low`: 低Thinkingモード
- カスタムThinkingバジェット設定に対応
- 思考内容と最終回答の自動分離

### 🔍 検索拡張モデル
- `gemini-2.5-pro-search`: 検索機能統合モデル

### 🖼️ 画像生成モデル（Antigravity）
- `gemini-3.1-flash-image`: 基本画像生成モデル
- **解像度サフィックス**:
  - `-2k`: 2K解像度
  - `-4k`: 4K HD解像度
- **アスペクト比サフィックス**:
  - `-1x1`: 正方形（アバター）
  - `-16x9`: 横長（デスクトップ壁紙）
  - `-9x16`: 縦長（モバイル壁紙）
  - `-21x9`: ウルトラワイド（ウルトラワイドモニター）
  - `-4x3`: 従来のディスプレイ
  - `-3x4`: 縦型ポスター
- **組み合わせ例**:
  - `gemini-3.1-flash-image-4k-16x9`: 4K横長
  - `gemini-3.1-flash-image-2k-9x16`: 2K縦長
- 比率未指定時はAPIが自動的にアスペクト比を決定

### 🌊 特殊機能バリアント
- **疑似ストリーミングモード**: 任意のモデル名に `-假流式` サフィックスを追加
  - 例: `gemini-2.5-pro-假流式`
  - ストリーミングレスポンスが必要だがサーバーが真のストリーミングに対応していない場合に使用
- **ストリーミング途切れ防止モード**: モデル名に `流式抗截断/` プレフィックスを追加
  - 例: `流式抗截断/gemini-2.5-pro`
  - レスポンスの途切れを自動検出しリトライして完全な回答を保証

### 🔧 モデル機能の自動検出
- システムがモデル名内の機能識別子を自動認識
- 機能モード切替を透過的に処理
- 機能の組み合わせ使用に対応


---

## インストールガイド

### Termux環境

**初期インストール**
```bash
curl -o termux-install.sh "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/termux-install.sh" && chmod +x termux-install.sh && ./termux-install.sh
```

**サービス再起動**
```bash
cd gcli2api
bash termux-start.sh
```

### Windows環境

**初期インストール**
```powershell
iex (iwr "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/install.ps1" -UseBasicParsing).Content
```

**サービス再起動**
`start.bat` をダブルクリックして実行

### Linux環境

**初期インストール**
```bash
curl -o install.sh "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/install.sh" && chmod +x install.sh && ./install.sh
```

**サービス再起動**
```bash
cd gcli2api
bash start.sh
```

### macOS環境

**初期インストール**
```bash
curl -o darwin-install.sh "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/darwin-install.sh" && chmod +x darwin-install.sh && ./darwin-install.sh
```

**サービス再起動**
```bash
cd gcli2api
bash start.sh
```

### Docker環境

**Docker Runコマンド**
```bash
# 共通パスワードを使用
docker run -d --name gcli2api --network host -e PASSWORD=pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/gcli2api:latest

# 個別パスワードを使用
docker run -d --name gcli2api --network host -e API_PASSWORD=api_pwd -e PANEL_PASSWORD=panel_pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/gcli2api:latest
```

**Docker Mac**
```bash
# 共通パスワードを使用
docker run -d \
  --name gcli2api \
  -p 7861:7861 \
  -p 8080:8080 \
  -e PASSWORD=pwd \
  -e PORT=7861 \
  -v "$(pwd)/data/creds":/app/creds \
  ghcr.io/su-kaka/gcli2api:latest
```

```bash
# 個別パスワードを使用
docker run -d \
--name gcli2api \
-p 7861:7861 \
-p 8080:8080 \
-e API_PASSWORD=api_pwd \
-e PANEL_PASSWORD=panel_pwd \
-e PORT=7861 \
-v $(pwd)/data/creds:/app/creds \
ghcr.io/su-kaka/gcli2api:latest
```

**Docker Compose Runコマンド**
1. 以下の内容を `docker-compose.yml` ファイルとして保存:
    ```yaml
    version: '3.8'

    services:
      gcli2api:
        image: ghcr.io/su-kaka/gcli2api:latest
        container_name: gcli2api
        restart: unless-stopped
        network_mode: host
        environment:
          # 共通パスワードを使用（シンプルなデプロイに推奨）
          - PASSWORD=pwd
          - PORT=7861
          # または個別パスワードを使用（本番環境に推奨）
          # - API_PASSWORD=your_api_password
          # - PANEL_PASSWORD=your_panel_password
        volumes:
          - ./data/creds:/app/creds
        healthcheck:
          test: ["CMD-SHELL", "python -c \"import sys, urllib.request, os; port = os.environ.get('PORT', '7861'); req = urllib.request.Request(f'http://localhost:{port}/v1/models', headers={'Authorization': 'Bearer ' + os.environ.get('PASSWORD', 'pwd')}); sys.exit(0 if urllib.request.urlopen(req, timeout=5).getcode() == 200 else 1)\""]
          interval: 30s
          timeout: 10s
          retries: 3
          start_period: 40s
    ```
2. サービスを起動:
    ```bash
    docker-compose up -d
    ```

---

## 設定手順

1. `http://127.0.0.1:7861` にアクセス（デフォルトポート、PORT環境変数で変更可能）
2. OAuth認証フローを完了（デフォルトパスワード: `pwd`、環境変数で変更可能）
   - **GCLIモード**: Google Cloud Gemini APIクレデンシャルの取得用
   - **Antigravityモード**: Google Antigravity APIクレデンシャルの取得用
3. クライアントを設定:

**OpenAI互換クライアント:**
   - **エンドポイントアドレス**: `http://127.0.0.1:7861/v1`
   - **APIキー**: `pwd`（デフォルト値、API_PASSWORDまたはPASSWORD環境変数で変更可能）

**Geminiネイティブクライアント:**
   - **エンドポイントアドレス**: `http://127.0.0.1:7861`
   - **認証方式**:
     - `Authorization: Bearer your_api_password`
     - `x-goog-api-key: your_api_password`
     - URLパラメータ: `?key=your_api_password`

### 🌟 デュアル認証モード対応

**GCLI認証モード**
- 標準Google Cloud Gemini API認証
- OAuth2.0認証フローに対応
- 必要なGoogle Cloud APIを自動的に有効化

**Antigravity認証モード**
- Google Antigravity API専用認証
- 独立したクレデンシャル管理システム
- 一括アップロードと管理に対応
- GCLIクレデンシャルとは完全に分離

**統合管理インターフェース**
- 「一括アップロード」タブで両方のクレデンシャルタイプを管理
- 上部セクション: GCLIクレデンシャル一括アップロード（青テーマ）
- 下部セクション: Antigravityクレデンシャル一括アップロード（緑テーマ）
- 各タイプ別のクレデンシャル管理タブ

## 💾 データストレージモード

### 🌟 ストレージバックエンド対応

gcli2apiは2つのストレージバックエンドに対応: **ローカルSQLite（デフォルト）** と **MongoDB（クラウド分散ストレージ）**

### 📁 ローカルSQLiteストレージ（デフォルト）

**デフォルトストレージ方式**
- 設定不要、すぐに利用可能
- データはローカルSQLiteデータベースに保存
- 単一マシンデプロイおよび個人利用に最適
- データベースファイルの自動作成・管理

### 🍃 MongoDBクラウドストレージモード

**クラウド分散ストレージソリューション**

マルチインスタンスデプロイやクラウドストレージが必要な場合、MongoDBストレージモードを有効にできます。

### ⚙️ MongoDBモードの有効化

**ステップ1: MongoDB接続の設定**
```bash
# ローカルMongoDB
export MONGODB_URI="mongodb://localhost:27017"

# MongoDB Atlasクラウドサービス
export MONGODB_URI="mongodb+srv://username:password@cluster.mongodb.net"

# 認証付きMongoDB
export MONGODB_URI="mongodb://admin:password@localhost:27017/admin"

# オプション: カスタムデータベース名（デフォルト: gcli2api）
export MONGODB_DATABASE="my_gcli_db"
```

**ステップ2: アプリケーションの起動**
```bash
# アプリケーションがMongoDB設定を自動検出し、MongoDBストレージを使用します
python web.py
```

**Docker環境でのMongoDB使用**
```bash
# 単一MongoDBデプロイ
docker run -d --name gcli2api \
  -e MONGODB_URI="mongodb://mongodb:27017" \
  -e API_PASSWORD=your_password \
  --network your_network \
  ghcr.io/su-kaka/gcli2api:latest

# MongoDB Atlasの使用
docker run -d --name gcli2api \
  -e MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/gcli2api" \
  -e API_PASSWORD=your_password \
  -p 7861:7861 \
  ghcr.io/su-kaka/gcli2api:latest
```

**Docker Composeの例**
```yaml
version: '3.8'

services:
  mongodb:
    image: mongo:7
    container_name: gcli2api-mongodb
    restart: unless-stopped
    environment:
      MONGO_INITDB_ROOT_USERNAME: admin
      MONGO_INITDB_ROOT_PASSWORD: password123
    volumes:
      - mongodb_data:/data/db
    ports:
      - "27017:27017"

  gcli2api:
    image: ghcr.io/su-kaka/gcli2api:latest
    container_name: gcli2api
    restart: unless-stopped
    depends_on:
      - mongodb
    environment:
      - MONGODB_URI=mongodb://admin:password123@mongodb:27017/admin
      - MONGODB_DATABASE=gcli2api
      - API_PASSWORD=your_api_password
      - PORT=7861
    ports:
      - "7861:7861"

volumes:
  mongodb_data:
```


### 🔧 高度な設定

**MongoDB接続の最適化**
```bash
# コネクションプールとタイムアウト設定
export MONGODB_URI="mongodb://localhost:27017?maxPoolSize=10&serverSelectionTimeoutMS=5000"

# レプリカセット設定
export MONGODB_URI="mongodb://host1:27017,host2:27017,host3:27017/gcli2api?replicaSet=myReplicaSet"

# リード・ライト分離設定
export MONGODB_URI="mongodb://localhost:27017/gcli2api?readPreference=secondaryPreferred"
```

### 環境変数設定

**基本設定**
- `PORT`: サービスポート（デフォルト: 7861）
- `HOST`: サーバーリッスンアドレス（デフォルト: 0.0.0.0）

**パスワード設定**
- `API_PASSWORD`: チャットAPIアクセスパスワード（デフォルト: PASSWORDまたはpwdを継承）
- `PANEL_PASSWORD`: コントロールパネルアクセスパスワード（デフォルト: PASSWORDまたはpwdを継承）
- `PASSWORD`: 共通パスワード、設定時に上記2つを上書き（デフォルト: pwd）

**パフォーマンスと安定性の設定**
- `RETRY_429_ENABLED`: 429エラー自動リトライの有効化（デフォルト: true）
- `RETRY_429_MAX_RETRIES`: 429エラーの最大リトライ回数（デフォルト: 3）
- `RETRY_429_INTERVAL`: 429エラーのリトライ間隔、秒単位（デフォルト: 1.0）
- `ANTI_TRUNCATION_MAX_ATTEMPTS`: 途切れ防止の最大リトライ回数（デフォルト: 3）

**ネットワークとプロキシ設定**
- `PROXY`: HTTP/HTTPSプロキシアドレス（形式: `http://host:port`）
- `OAUTH_PROXY_URL`: OAuth認証プロキシエンドポイント
- `GOOGLEAPIS_PROXY_URL`: Google APIsプロキシエンドポイント
- `METADATA_SERVICE_URL`: メタデータサービスプロキシエンドポイント

**自動化設定**
- `AUTO_BAN`: クレデンシャル自動BANの有効化（デフォルト: true）
- `AUTO_LOAD_ENV_CREDS`: 起動時に環境変数クレデンシャルを自動ロード（デフォルト: false）

**互換性設定**
- `COMPATIBILITY_MODE`: 互換モードの有効化、systemメッセージをuserメッセージに変換（デフォルト: false）

**ログ設定**
- `LOG_LEVEL`: ログレベル（DEBUG/INFO/WARNING/ERROR、デフォルト: INFO）
- `LOG_FILE`: ログファイルパス（デフォルト: log.txt）

**ストレージ設定**

**SQLite設定（デフォルト）**
- 設定不要、自動的にローカルSQLiteデータベースを使用
- データベースファイルはプロジェクトディレクトリに自動作成

**MongoDB設定（オプションのクラウドストレージ）**
- `MONGODB_URI`: MongoDB接続文字列（設定時にMongoDBモードを有効化）
- `MONGODB_DATABASE`: MongoDBデータベース名（デフォルト: gcli2api）

**Docker使用例**
```bash
# 共通パスワードを使用
docker run -d --name gcli2api \
  -e PASSWORD=mypassword \
  -e PORT=7861 \
  ghcr.io/su-kaka/gcli2api:latest

# 個別パスワードを使用
docker run -d --name gcli2api \
  -e API_PASSWORD=my_api_password \
  -e PANEL_PASSWORD=my_panel_password \
  -e PORT=7861 \
  ghcr.io/su-kaka/gcli2api:latest
```

注意: クレデンシャル環境変数が設定されている場合、システムは環境変数のクレデンシャルを優先的に使用し、`creds` ディレクトリ内のファイルを無視します。

### API使用方法

本サービスは複数の完全なAPIエンドポイントセットに対応しています:

#### 1. OpenAI互換エンドポイント（GCLI）

**エンドポイント:** `/v1/chat/completions`
**認証:** `Authorization: Bearer your_api_password`

2つのリクエストフォーマットに対応し、自動検出・処理を行います:

**OpenAIフォーマット:**
```json
{
  "model": "gemini-2.5-pro",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant"},
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.7,
  "stream": true
}
```

**Geminiネイティブフォーマット:**
```json
{
  "model": "gemini-2.5-pro",
  "contents": [
    {"role": "user", "parts": [{"text": "Hello"}]}
  ],
  "systemInstruction": {"parts": [{"text": "You are a helpful assistant"}]},
  "generationConfig": {
    "temperature": 0.7
  }
}
```

#### 2. Geminiネイティブエンドポイント（GCLI）

**非ストリーミングエンドポイント:** `/v1/models/{model}:generateContent`
**ストリーミングエンドポイント:** `/v1/models/{model}:streamGenerateContent`
**モデル一覧:** `/v1/models`

**認証方式（いずれか1つを選択）:**
- `Authorization: Bearer your_api_password`
- `x-goog-api-key: your_api_password`
- URLパラメータ: `?key=your_api_password`

**リクエスト例:**
```bash
# x-goog-api-keyヘッダーを使用
curl -X POST "http://127.0.0.1:7861/v1/models/gemini-2.5-pro:generateContent" \
  -H "x-goog-api-key: your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'

# URLパラメータを使用
curl -X POST "http://127.0.0.1:7861/v1/models/gemini-2.5-pro:streamGenerateContent?key=your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'
```

#### 3. Claude APIフォーマットエンドポイント

**エンドポイント:** `/v1/messages`
**認証:** `x-api-key: your_api_password` または `Authorization: Bearer your_api_password`

**リクエスト例:**
```bash
curl -X POST "http://127.0.0.1:7861/v1/messages" \
  -H "x-api-key: your_api_password" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.5-pro",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello, Claude!"}
    ]
  }'
```

**systemパラメータの対応:**
```json
{
  "model": "gemini-2.5-pro",
  "max_tokens": 1024,
  "system": "You are a helpful assistant",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

**注意事項:**
- Claude APIフォーマット仕様に完全互換
- バックエンド呼び出し時にGeminiフォーマットへ自動変換
- すべてのClaude標準パラメータに対応
- レスポンスフォーマットはClaude API仕様に準拠

#### 4. Antigravity APIエンドポイント

**OpenAI、Gemini、Claudeの3フォーマットに対応**

##### Antigravity OpenAIフォーマットエンドポイント

**エンドポイント:** `/antigravity/v1/chat/completions`
**認証:** `Authorization: Bearer your_api_password`

**リクエスト例:**
```bash
curl -X POST "http://127.0.0.1:7861/antigravity/v1/chat/completions" \
  -H "Authorization: Bearer your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [
      {"role": "user", "content": "Hello"}
    ],
    "stream": true
  }'
```

##### Antigravity Geminiフォーマットエンドポイント

**非ストリーミングエンドポイント:** `/antigravity/v1/models/{model}:generateContent`
**ストリーミングエンドポイント:** `/antigravity/v1/models/{model}:streamGenerateContent`

**認証方式（いずれか1つを選択）:**
- `Authorization: Bearer your_api_password`
- `x-goog-api-key: your_api_password`
- URLパラメータ: `?key=your_api_password`

**リクエスト例:**
```bash
# Geminiフォーマット非ストリーミングリクエスト
curl -X POST "http://127.0.0.1:7861/antigravity/v1/models/claude-sonnet-4-5:generateContent" \
  -H "x-goog-api-key: your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ],
    "generationConfig": {
      "temperature": 0.7
    }
  }'

# Geminiフォーマットストリーミングリクエスト
curl -X POST "http://127.0.0.1:7861/antigravity/v1/models/gemini-2.5-flash:streamGenerateContent?key=your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'
```

##### Antigravity Claudeフォーマットエンドポイント

**エンドポイント:** `/antigravity/v1/messages`
**認証:** `x-api-key: your_api_password`

**リクエスト例:**
```bash
curl -X POST "http://127.0.0.1:7861/antigravity/v1/messages" \
  -H "x-api-key: your_api_password" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello"}
    ]
  }'
```

**対応Antigravityモデル:**
- Claudeシリーズ: `claude-sonnet-4-5`、`claude-opus-4-5` など
- Geminiシリーズ: `gemini-2.5-flash`、`gemini-2.5-pro` など
- Thinkingモデルに自動対応

**Geminiネイティブの例:**
```python
from io import BytesIO
from PIL import Image
from google.genai import Client
from google.genai.types import HttpOptions
from google.genai import types
# クライアントは環境変数 `GEMINI_API_KEY` からAPIキーを取得します。

client = Client(
            api_key="pwd",
            http_options=HttpOptions(base_url="http://127.0.0.1:7861"),
        )

prompt = (
    """
    猫を描いて
    """
)

response = client.models.generate_content(
    model="gemini-3.1-flash-image",
    contents=[prompt],
    config=types.GenerateContentConfig(
        image_config=types.ImageConfig(
            aspect_ratio="16:9",
        )
    )
)
for part in response.candidates[0].content.parts:
    if part.text is not None:
        print(part.text)
    elif part.inline_data is not None:
        image = Image.open(BytesIO(part.inline_data.data))
        image.save("generated_image.png")

```

**注意事項:**
- OpenAIエンドポイントはOpenAI互換フォーマットを返します
- GeminiエンドポイントはGeminiネイティブフォーマットを返します
- 両エンドポイントとも同一のAPIパスワードを使用

## 📋 完全なAPIリファレンス

### Webコンソール API

**認証エンドポイント**
- `POST /auth/login` - ユーザーログイン
- `POST /auth/start` - OAuth認証の開始（GCLIおよびAntigravityモード対応）
- `POST /auth/callback` - OAuthコールバックの処理
- `POST /auth/callback-url` - コールバックURLから直接認証を完了
- `GET /auth/status/{project_id}` - 認証ステータスの確認

**クレデンシャル管理エンドポイント**（`mode=geminicli` または `mode=antigravity` パラメータ対応）
- `POST /creds/upload` - クレデンシャルファイルの一括アップロード（JSONおよびZIP対応）
- `GET /creds/status` - クレデンシャルステータス一覧の取得（ページネーションとフィルタリング対応）
- `GET /creds/detail/{filename}` - 単一クレデンシャルの詳細取得
- `POST /creds/action` - 単一クレデンシャル操作（有効化/無効化/削除）
- `POST /creds/batch-action` - クレデンシャルの一括操作
- `GET /creds/download/{filename}` - 単一クレデンシャルファイルのダウンロード
- `GET /creds/download-all` - 全クレデンシャルの一括ダウンロード
- `POST /creds/fetch-email/{filename}` - ユーザーメールの取得
- `POST /creds/refresh-all-emails` - ユーザーメールの一括更新
- `POST /creds/deduplicate-by-email` - メールによるクレデンシャルの重複排除
- `POST /creds/verify-project/{filename}` - クレデンシャルのProject ID検証
- `GET /creds/quota/{filename}` - クレデンシャルのクォータ情報取得（Antigravityのみ）

**設定管理エンドポイント**
- `GET /config/get` - 現在の設定の取得
- `POST /config/save` - 設定の保存

**ログ管理エンドポイント**
- `POST /logs/clear` - ログのクリア
- `GET /logs/download` - ログファイルのダウンロード
- `WebSocket /logs/stream` - リアルタイムログストリーム

**バージョン情報エンドポイント**
- `GET /version/info` - バージョン情報の取得（オプション `check_update=true` パラメータで更新確認）

### チャットAPI機能

**マルチモーダル対応**
```json
{
  "model": "gemini-2.5-pro",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "この画像を説明してください"},
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/jpeg;base64,/9j/4AAQSkZJRgABA..."
          }
        }
      ]
    }
  ]
}
```

**Thinkingモード対応**
```json
{
  "model": "gemini-2.5-pro-high",
  "messages": [
    {"role": "user", "content": "複雑な数学の問題"}
  ]
}
```

レスポンスには分離された思考内容が含まれます:
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "最終回答",
      "reasoning_content": "詳細な思考プロセス..."
    }
  }]
}
```

**ストリーミング途切れ防止の使用方法**
```json
{
  "model": "流式抗截断/gemini-2.5-pro",
  "messages": [
    {"role": "user", "content": "長い記事を書いてください"}
  ],
  "stream": true
}
```

**互換モード**
```bash
# 互換モードを有効化
export COMPATIBILITY_MODE=true
```
このモードでは、すべての `system` メッセージが `user` メッセージに変換され、特定のクライアントとの互換性が向上します。

---

## 💬 コミュニティ

QQグループへの参加をお待ちしています！

**QQグループ: 1083250744**

<img src="qq群.jpg" width="200" alt="QQグループQRコード">

---

## ライセンスと免責事項

本プロジェクトは学習および研究目的のみに使用できます。本プロジェクトの使用は、以下に同意したことを意味します:
- 本プロジェクトをいかなる商用目的にも使用しないこと
- 本プロジェクトの使用に伴うすべてのリスクと責任を負うこと
- 関連するサービス利用規約および法的規制を遵守すること

プロジェクトの作者は、本プロジェクトの使用から生じるいかなる直接的または間接的な損害についても責任を負いません。
