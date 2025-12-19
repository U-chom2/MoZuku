# MoZuku LSP (Python/GinZA版)

日本語NLP Language Server - GinZAを使用したPython実装版です。

MeCab/CaboChaの代わりにGinZA (spaCy) を使用することで、より簡単なインストールと高品質な解析を実現します。

## インストール

### 1. GinZAモデルのインストール

標準モデル（高速）:
```bash
pip install ginza ja-ginza
```

Transformerモデル（高精度）:
```bash
pip install ginza ja-ginza-electra
```

### 2. LSPサーバーのインストール

```bash
cd mozuku-lsp-py
pip install -e .
```

## 使用方法

### コマンドラインから起動

```bash
mozuku-lsp
```

または:

```bash
python -m mozuku_lsp.server
```

### VSCode拡張との連携

VSCode拡張 (vscode-mozuku) は自動的にPython版サーバーを検出します。
Python版が見つからない場合は、C++版にフォールバックします。

## 機能

### 形態素解析
- GinZA/SudachiPyによる高精度な分かち書き
- 品詞情報、読み、基本形の取得
- セマンティックトークンハイライト

### 係り受け解析
- Universal Dependencies形式の依存構造解析
- 文節単位の係り受け情報

### 文法チェック
以下の文法ルールをサポート:
- 読点過多の検出
- 逆接の「が」の重複
- 同一助詞の連続
- 助詞の隣接
- 接続詞の重複
- ら抜き言葉の検出

### その他の機能
- コメント内日本語テキストの解析（Python, JavaScript, C++, Rust, HTML, LaTeX対応）
- Wikipedia連携（名詞ホバー時にサマリ表示）

## 設定

VSCode設定で以下のオプションが利用可能:

| 設定 | 説明 | デフォルト |
|------|------|-----------|
| `mozuku.model` | GinZAモデル | `ja_ginza` |
| `mozuku.analysis.grammarCheck` | 文法チェック有効化 | `true` |
| `mozuku.analysis.rules.commaLimit` | 読点制限 | `true` |
| `mozuku.analysis.rules.raDropping` | ら抜き検出 | `true` |

## デバッグ

デバッグログを有効にするには:

```bash
MOZUKU_DEBUG=1 mozuku-lsp
```

## ライセンス

MIT License
