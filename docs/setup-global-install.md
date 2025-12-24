# MoZuku LSP グローバルインストール設定

## 概要

VSCode拡張機能を開発モード（F5起動）ではなく、常時起動させるための設定を行った。

## 変更点

### 1. pygls 2.0 API 互換性修正 (server.py)

`mozuku-lsp-py` が使用する `pygls` が 2.0 にアップグレードされ、API が変更されたため以下を修正：

| 旧 API | 新 API | 箇所 |
|--------|--------|------|
| `server.publish_diagnostics(uri, diagnostics)` | `server.text_document_publish_diagnostics(lsp.PublishDiagnosticsParams(...))` | 348行目 |
| `server.send_notification(method, params)` | `server.protocol.notify(method, params)` | 514, 530, 537, 553行目 |
| `lsp.TextDocumentContentChangeEvent_Type1` | `lsp.TextDocumentContentChangePartial` | 208行目 |

### 2. VSCode拡張機能のPATH修正 (client.ts)

VSCodeをGUIから起動すると、シェルの`.zshrc`等が読み込まれず`~/.local/bin`がPATHに含まれない。
そのため `resolvePythonServerPath()` 関数に `~/.local/bin/mozuku-lsp` を直接探索するコードを追加：

```typescript
const homeDir = process.env.HOME || process.env.USERPROFILE || '';
const uvToolPaths = [
  path.join(homeDir, '.local', 'bin', 'mozuku-lsp'),
  path.join(homeDir, '.local', 'bin', 'mozuku-lsp.exe'),
];
```

### 3. Python環境変数のクリーンアップ (client.ts)

VSCodeが設定するPYTHONPATHやPYTHONHOME環境変数が、uv tool環境の分離されたPython環境に干渉する問題を修正。
LSPサーバー起動時にこれらの環境変数を削除：

```typescript
// Create a clean environment for the LSP server
// Remove Python-related env vars that could interfere with the isolated uv tool environment
const cleanEnv = { ...process.env };
delete cleanEnv.PYTHONPATH;
delete cleanEnv.PYTHONHOME;
```

### 4. tree-sitter バージョン制約 (pyproject.toml)

`tree-sitter-languages 1.10.x` は `tree-sitter 0.25.x` と互換性がないため、バージョンを制約：

```toml
dependencies = [
    ...
    "tree-sitter>=0.21.0,<0.22.0",  # tree-sitter-languages との互換性のため
    ...
]
```

### 5. バイトオフセット修正 (comment_extractor.py)

UTF-8テキストのコメント抽出で、バイトオフセットと文字オフセットの混同を修正：

```python
# 修正前（バグ）
comment_text = text[start_byte:end_byte]

# 修正後
text_bytes = text.encode("utf-8")
comment_text = text_bytes[start_byte:end_byte].decode("utf-8", errors="replace")
```

### 6. Wikipedia API修正 (wikipedia.py)

`httpx`ライブラリがWikipedia APIから403エラーを受ける問題を修正。
`requests`ライブラリに変更：

```python
# 修正前
import httpx
with httpx.Client() as client:
    response = client.get(url, ...)

# 修正後
import requests
response = requests.get(url, ...)
```

### LSPサーバー探索順序

`client.ts` の `resolvePythonServerPath()` 関数は以下の順序でLSPサーバーを探索する：

1. ワークスペース内の `mozuku-lsp-py/.venv` のPython
2. `~/.local/bin/mozuku-lsp` (uv tool install先) ← **今回追加**
3. PATHからの `mozuku-lsp` コマンド
4. システムPythonの `mozuku_lsp` モジュール

### 実行した手順

#### 1. mozuku-lsp コマンドのグローバルインストール

```bash
cd /Users/y.okumura/private_workspace/MoZuku/mozuku-lsp-py
uv tool install . --python 3.12
```

- `--python 3.12` は `tree-sitter-languages` がPython 3.13に未対応のため必要
- インストール先: `/Users/y.okumura/.local/bin/mozuku-lsp`

#### 2. VSCode拡張機能のシンボリックリンク作成

```bash
cd /Users/y.okumura/private_workspace/MoZuku/vscode-mozuku
npm run compile
ln -sfn /Users/y.okumura/private_workspace/MoZuku/vscode-mozuku ~/.vscode/extensions/mozuku
```

### 再インストール手順（コード変更後）

`mozuku-lsp-py` のコードを変更した後は、以下の手順でグローバルインストールを更新する：

```bash
uv tool uninstall mozuku-lsp
uv cache clean mozuku-lsp
uv tool install /Users/y.okumura/private_workspace/MoZuku/mozuku-lsp-py --python 3.12
```

**注意**: `uv cache clean` を実行しないとキャッシュされた古いビルドが使われる。

## 結果

VSCodeを再起動すると、どのディレクトリでもMoZuku LSPが自動的に起動する。

## 注意事項

- シンボリックリンク方式のため、`vscode-mozuku` のコードを変更した場合は `npm run compile` を再実行する必要がある
- 正式リリース時は `vsce package` でvsixファイルを作成してインストールする方が良い

## 代替方法: vsixパッケージでのインストール

```bash
cd /Users/y.okumura/private_workspace/MoZuku/vscode-mozuku
npm run package
npx @vscode/vsce package --allow-missing-repository --baseContentUrl https://github.com/your-repo
code --install-extension mozuku-0.1.0.vsix
```

※ `package.json` に `repository` フィールドを追加すると `--baseContentUrl` オプションが不要になる
