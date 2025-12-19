# MoZuku - Japanese NLP Language Server

GinZa（spaCy）を活用した日本語文章の解析・校正を行う LSP サーバーと VSCode/Cursor 拡張機能です。

## 特徴

- **形態素解析**: GinZa による高精度な日本語トークン化
- **文法チェック**:
  - 読点の使いすぎ検出
  - 逆接の「が」の重複検出
  - 同一助詞の連続使用検出
  - 助詞の隣接検出
  - 接続詞の連続使用検出
  - ら抜き言葉の検出
- **セマンティックハイライト**: 品詞ごとの色分け表示（名詞、動詞、形容詞、助詞など）
- **コメント内解析**: C/C++/Python/JavaScript/TypeScript/Rust/HTML/LaTeX のコメント・本文内日本語を解析
- **ホバー情報**: 単語の原形、読み、品詞情報、Wikipedia のサマリーを表示

## 構成

```
MoZuku/
├── mozuku-lsp-py/     # Python LSP サーバー (GinZa ベース)
└── vscode-mozuku/     # VSCode/Cursor 拡張機能
```

## インストール

### 1. LSP サーバーのインストール

Python 3.10〜3.12 が必要です（3.13 は tree-sitter-languages が未対応）。

```bash
# uv を使用（推奨）
cd mozuku-lsp-py
uv tool install . --python 3.12

# または pip を使用
pip install .
```

インストール後、`mozuku-lsp` コマンドが使用可能になります。

### 2. VSCode/Cursor 拡張機能のインストール

#### 方法 A: シンボリックリンク（開発用）

```bash
# ビルド
cd vscode-mozuku
npm install
npm run compile

# VSCode 用
ln -sfn "$(pwd)" ~/.vscode/extensions/mozuku

# Cursor 用
ln -sfn "$(pwd)" ~/.cursor/extensions/mozuku
```

#### 方法 B: vsix パッケージ（配布用）

```bash
cd vscode-mozuku
npm install
npm run package
npx @vscode/vsce package --allow-missing-repository

# インストール
code --install-extension mozuku-0.1.0.vsix      # VSCode
cursor --install-extension mozuku-0.1.0.vsix    # Cursor
```

### 3. エディタを再起動

VSCode または Cursor を再起動すると、MoZuku LSP が自動的に起動します。

## 対応ファイル

- `.ja.txt`, `.ja.md` - 日本語専用ファイル（全文解析）
- `.txt`, `.md` - プレーンテキスト・Markdown（日本語比率が一定以上の場合）
- `.c`, `.cpp`, `.py`, `.js`, `.ts`, `.tsx`, `.rs` - コメント内の日本語を解析
- `.html` - タグ内テキストを解析
- `.tex` - LaTeX 本文を解析

## 設定

VSCode/Cursor の設定で以下のオプションを変更できます：

| 設定                                   | デフォルト   | 説明                                                             |
| -------------------------------------- | ------------ | ---------------------------------------------------------------- |
| `mozuku.model`                       | `ja_ginza` | 使用する GinZa モデル (`ja_ginza` または `ja_ginza_electra`) |
| `mozuku.analysis.grammarCheck`       | `true`     | 文法チェックの有効/無効                                          |
| `mozuku.analysis.minJapaneseRatio`   | `0.1`      | 解析対象とする最小日本語比率                                     |
| `mozuku.analysis.warningMinSeverity` | `2`        | 警告の最小重要度 (1=Error, 2=Warning, 3=Info, 4=Hint)            |

### 個別ルールの設定

| 設定                                               | デフォルト | 説明                 |
| -------------------------------------------------- | ---------- | -------------------- |
| `mozuku.analysis.rules.commaLimit`               | `true`   | 読点制限ルール       |
| `mozuku.analysis.rules.commaLimitMax`            | `3`      | 一文の読点最大数     |
| `mozuku.analysis.rules.adversativeGa`            | `true`   | 逆接「が」の重複検出 |
| `mozuku.analysis.rules.duplicateParticleSurface` | `true`   | 同一助詞の連続検出   |
| `mozuku.analysis.rules.adjacentParticles`        | `true`   | 助詞の隣接検出       |
| `mozuku.analysis.rules.conjunctionRepeat`        | `true`   | 接続詞の連続検出     |
| `mozuku.analysis.rules.raDropping`               | `true`   | ら抜き言葉の検出     |

## セマンティックハイライトの色

| 品詞   | 色                           |
| ------ | ---------------------------- |
| 名詞   | `#ff7b4f` (スカーレット)         |
| 動詞   | `#569cd6` (青)             |
| 形容詞 | `#4fc1ff` (シアン)         |
| 副詞   | `#9cdcfe` (ライトブルー)   |
| 助詞   | `#d16969` (赤)             |
| 助動詞 | `#87CEEB` (スカイブルー)   |
| 接続詞 | `#d7ba7d` (ゴールド)       |
| 感動詞 | `#b5cea8` (ライトグリーン) |
