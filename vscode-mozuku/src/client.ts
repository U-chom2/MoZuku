import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as cp from 'child_process';
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
  State,
} from 'vscode-languageclient/node';

type CommentHighlightMessage = {
  uri: string;
  ranges: Array<{
    start: { line: number; character: number };
    end: { line: number; character: number };
  }>;
};

type ContentHighlightMessage = {
  uri: string;
  ranges: Array<{
    start: { line: number; character: number };
    end: { line: number; character: number };
  }>;
};

type SemanticHighlightMessage = {
  uri: string;
  tokens: Array<{
    range: {
      start: { line: number; character: number };
      end: { line: number; character: number };
    };
    type: string;
    modifiers: number;
  }>;
};

const supportedLanguages = [
  'japanese',
  'plaintext',
  'markdown',
  'c',
  'cpp',
  'html',
  'python',
  'javascript',
  'javascriptreact',
  'typescript',
  'typescriptreact',
  'rust',
  'latex',
];

export async function startClient(
  ctx: vscode.ExtensionContext,
  _serverPath: string
) {
  const isDebug = process.env.VSCODE_DEBUG_MODE === 'true' || ctx.extensionMode === vscode.ExtensionMode.Development;

  const pythonServerInfo = resolvePythonServerPath(ctx);

  if (!pythonServerInfo.available) {
    const msg = `MoZuku LSPサーバーが見つかりません。mozuku-lsp-py をインストールしてください。`;
    console.error('[MoZuku]', msg);
    vscode.window.showErrorMessage(msg);
    throw new Error(msg);
  }

  console.log('[MoZuku] Python LSPサーバーを使用:', pythonServerInfo.command, pythonServerInfo.args);

  // Create a clean environment for the LSP server
  // Remove Python-related env vars that could interfere with the isolated uv tool environment
  const cleanEnv = { ...process.env };
  delete cleanEnv.PYTHONPATH;
  delete cleanEnv.PYTHONHOME;

  const serverOptions: ServerOptions = {
    run: {
      command: pythonServerInfo.command,
      args: pythonServerInfo.args,
      transport: TransportKind.stdio,
      options: { env: isDebug ? { ...cleanEnv, MOZUKU_DEBUG: '1' } : cleanEnv }
    },
    debug: {
      command: pythonServerInfo.command,
      args: pythonServerInfo.args,
      transport: TransportKind.stdio,
      options: { env: { ...cleanEnv, MOZUKU_DEBUG: '1' } }
    },
  };

  const config = vscode.workspace.getConfiguration('mozuku');

  const initOptions = {
    model: config.get<string>('model', 'ja_ginza'),
    analysis: {
      grammarCheck: config.get<boolean>('analysis.grammarCheck', true),
      minJapaneseRatio: config.get<number>('analysis.minJapaneseRatio', 0.1),
      warningMinSeverity: config.get<number>('analysis.warningMinSeverity', 2),
      rules: {
        commaLimit: config.get<boolean>('analysis.rules.commaLimit', true),
        adversativeGa: config.get<boolean>('analysis.rules.adversativeGa', true),
        duplicateParticleSurface: config.get<boolean>('analysis.rules.duplicateParticleSurface', true),
        adjacentParticles: config.get<boolean>('analysis.rules.adjacentParticles', true),
        conjunctionRepeat: config.get<boolean>('analysis.rules.conjunctionRepeat', true),
        raDropping: config.get<boolean>('analysis.rules.raDropping', true),
        commaLimitMax: config.get<number>('analysis.rules.commaLimitMax', 3),
        adversativeGaMax: config.get<number>('analysis.rules.adversativeGaMax', 1),
        duplicateParticleSurfaceMaxRepeat: config.get<number>('analysis.rules.duplicateParticleSurfaceMaxRepeat', 1),
        adjacentParticlesMaxRepeat: config.get<number>('analysis.rules.adjacentParticlesMaxRepeat', 1),
        conjunctionRepeatMax: config.get<number>('analysis.rules.conjunctionRepeatMax', 1),
      }
    }
  };

  if (isDebug) {
    console.log('[MoZuku] LSP初期化オプション:', JSON.stringify(initOptions, null, 2));
  }

  const documentSelector = [
    ...supportedLanguages.map((language) => ({ language })),
    { scheme: 'file', pattern: '**/*.ja.txt' },
    { scheme: 'file', pattern: '**/*.ja.md' },
  ];

  const clientOptions: LanguageClientOptions = {
    documentSelector,
    synchronize: {
      fileEvents: vscode.workspace.createFileSystemWatcher('**/*'),
    },
    initializationOptions: initOptions,
    middleware: {},
  };

  const client = new LanguageClient(
    'mozuku',
    'MoZuku LSP',
    serverOptions,
    clientOptions
  );

  const semanticHighlights = new Map<string, Map<string, vscode.Range[]>>();
  const commentHighlights = new Map<string, vscode.Range[]>();
  const contentHighlights = new Map<string, vscode.Range[]>();

  const semanticColors: Record<string, string> = {
    noun: '#c8c8c8',
    verb: '#569cd6',
    adjective: '#4fc1ff',
    adverb: '#9cdcfe',
    particle: '#d16969',
    aux: '#87ceeb',
    conjunction: '#d7ba7d',
    symbol: '#808080',
    interj: '#b5cea8',
    prefix: '#c8c8c8',
    suffix: '#c8c8c8',
    unknown: '#aaaaaa',
  };

  const semanticDecorationTypes = new Map<string, vscode.TextEditorDecorationType>();
  const commentDecorationType = vscode.window.createTextEditorDecorationType({});
  const contentDecorationType = vscode.window.createTextEditorDecorationType({
  });
  ctx.subscriptions.push(commentDecorationType, contentDecorationType);

  const getSemanticDecorationType = (tokenType: string) => {
    if (!semanticDecorationTypes.has(tokenType)) {
      const color = semanticColors[tokenType] ?? '#cccccc';
      const decoration = vscode.window.createTextEditorDecorationType({
        color,
      });
      semanticDecorationTypes.set(tokenType, decoration);
      ctx.subscriptions.push(decoration);
    }
    return semanticDecorationTypes.get(tokenType)!;
  };

  const applyDecorationsToEditor = (editor: vscode.TextEditor | undefined) => {
    if (!editor) {
      return;
    }
    const uri = editor.document.uri.toString();

    const semanticByType = semanticHighlights.get(uri);
    if (semanticByType) {
      for (const [tokenType, ranges] of semanticByType) {
        const decoration = getSemanticDecorationType(tokenType);
        editor.setDecorations(decoration, ranges);
      }
    }
    for (const [tokenType, decoration] of semanticDecorationTypes) {
      if (!semanticByType || !semanticByType.has(tokenType)) {
        editor.setDecorations(decoration, []);
      }
    }

    const commentRanges = commentHighlights.get(uri) ?? [];
    editor.setDecorations(commentDecorationType, commentRanges);

    const contentRanges = contentHighlights.get(uri) ?? [];
    const hasSemantic = semanticByType && semanticByType.size > 0;
    if (contentRanges.length > 0 && !hasSemantic) {
      editor.setDecorations(contentDecorationType, contentRanges);
    } else {
      editor.setDecorations(contentDecorationType, []);
    }
  };

  const applyDecorationsForUri = (uri: string) => {
    for (const editor of vscode.window.visibleTextEditors) {
      if (editor.document.uri.toString() === uri) {
        applyDecorationsToEditor(editor);
      }
    }
  };

  const applyDecorationsToVisibleEditors = () => {
    for (const editor of vscode.window.visibleTextEditors) {
      applyDecorationsToEditor(editor);
    }
  };

  client.onDidChangeState((event) => {
    if (isDebug) {
      console.log(`[MoZuku] クライアント状態変更: ${State[event.oldState]} -> ${State[event.newState]}`);
    }
    if (event.newState === State.Running) {
      console.log('[MoZuku] LSPクライアントが起動しました');
    } else if (event.newState === State.Stopped) {
      console.error('[MoZuku] LSPクライアントが停止しました');
      if (event.oldState === State.Running) {
        vscode.window.showErrorMessage('MoZuku LSPサーバーが予期せず停止しました。サーバー実行ファイルを確認してください。');
      }
    }
  });

  client.onNotification('mozuku/commentHighlights', (payload: CommentHighlightMessage) => {
    const { uri, ranges = [] } = payload;
    const vsRanges = ranges.map((r) => {
      const start = new vscode.Position(r.start.line, r.start.character);
      const end = new vscode.Position(r.end.line, r.end.character);
      return new vscode.Range(start, end);
    });
    if (vsRanges.length === 0) {
      commentHighlights.delete(uri);
    } else {
      commentHighlights.set(uri, vsRanges);
    }
    applyDecorationsForUri(uri);
  });

  client.onNotification('mozuku/contentHighlights', (payload: ContentHighlightMessage) => {
    const { uri, ranges = [] } = payload;
    const vsRanges = ranges.map((r) => {
      const start = new vscode.Position(r.start.line, r.start.character);
      const end = new vscode.Position(r.end.line, r.end.character);
      return new vscode.Range(start, end);
    });
    if (vsRanges.length === 0) {
      contentHighlights.delete(uri);
    } else {
      contentHighlights.set(uri, vsRanges);
    }
    applyDecorationsForUri(uri);
  });

  client.onNotification('mozuku/semanticHighlights', (payload: SemanticHighlightMessage) => {
    const { uri, tokens = [] } = payload;
    if (tokens.length === 0) {
      semanticHighlights.delete(uri);
      applyDecorationsForUri(uri);
      return;
    }

    const perType = new Map<string, vscode.Range[]>();
    for (const token of tokens) {
      const start = new vscode.Position(token.range.start.line, token.range.start.character);
      const end = new vscode.Position(token.range.end.line, token.range.end.character);
      const range = new vscode.Range(start, end);

      const decoration = getSemanticDecorationType(token.type);
      if (!perType.has(token.type)) {
        perType.set(token.type, []);
      }
      perType.get(token.type)!.push(range);

      void decoration;
    }

    semanticHighlights.set(uri, perType);
    applyDecorationsForUri(uri);
  });

  if (isDebug) {
    client.outputChannel.show();
    console.log('[MoZuku] デバッグのためLSPクライアント出力チャンネルを表示');
  }

  ctx.subscriptions.push(client);

  try {
    await client.start();
    if (isDebug) {
      console.log('[MoZuku] LSPクライアントの起動に成功しました');
    }

    applyDecorationsToVisibleEditors();

    const openDisposable = vscode.workspace.onDidOpenTextDocument((doc) => {
      console.log('[MoZuku] ドキュメントを開きました:', {
        uri: doc.uri.toString(),
        languageId: doc.languageId,
        fileName: doc.fileName
      });
      applyDecorationsForUri(doc.uri.toString());
    });

    const activeEditorDisposable = vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor) {
        console.log('[MoZuku] アクティブエディタが変更されました:', {
          uri: editor.document.uri.toString(),
          languageId: editor.document.languageId,
          fileName: editor.document.fileName
        });
      }
      applyDecorationsToEditor(editor ?? undefined);
    });

    const visibleEditorsDisposable = vscode.window.onDidChangeVisibleTextEditors(() => {
      applyDecorationsToVisibleEditors();
    });

    const closeDisposable = vscode.workspace.onDidCloseTextDocument((doc) => {
      const uri = doc.uri.toString();
      semanticHighlights.delete(uri);
      commentHighlights.delete(uri);
      contentHighlights.delete(uri);
      applyDecorationsForUri(uri);
    });

    ctx.subscriptions.push(openDisposable, activeEditorDisposable, visibleEditorsDisposable, closeDisposable);
  } catch (error) {
    console.error('[MoZuku] LSPクライアントの起動に失敗しました:', error);
    vscode.window.showErrorMessage(`MoZuku LSPの起動に失敗: ${error}`);
    throw error;
  }

  return client;
}

interface PythonServerInfo {
  available: boolean;
  command: string;
  args: string[];
}

function resolvePythonServerPath(ctx: vscode.ExtensionContext): PythonServerInfo {
  const isDebug = process.env.VSCODE_DEBUG_MODE === 'true' || ctx.extensionMode === vscode.ExtensionMode.Development;
  const isWindows = process.platform === 'win32';
  const pythonExe = isWindows ? 'python.exe' : 'python';
  const venvBinDir = isWindows ? 'Scripts' : 'bin';

  // 1. Check for mozuku-lsp-py/.venv (priority: local development setup)
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const devPaths = [
    workspaceRoot ? path.join(workspaceRoot, 'mozuku-lsp-py') : null,
    path.join(ctx.extensionUri.fsPath, '..', 'mozuku-lsp-py'),
  ].filter(Boolean) as string[];

  for (const devPath of devPaths) {
    const venvPython = path.join(devPath, '.venv', venvBinDir, pythonExe);
    const serverPy = path.join(devPath, 'mozuku_lsp', 'server.py');

    if (fs.existsSync(venvPython) && fs.existsSync(serverPy)) {
      if (isDebug) {
        console.log(`[MoZuku] mozuku-lsp-py/.venv を検出: ${venvPython}`);
      }
      return {
        available: true,
        command: venvPython,
        args: ['-m', 'mozuku_lsp.server'],
      };
    }
  }

  // 2. Try mozuku-lsp command directly (if installed globally via uv tool install)
  // Check common installation paths first (VSCode GUI doesn't inherit shell PATH)
  const homeDir = process.env.HOME || process.env.USERPROFILE || '';
  const uvToolPaths = [
    path.join(homeDir, '.local', 'bin', 'mozuku-lsp'),
    path.join(homeDir, '.local', 'bin', 'mozuku-lsp.exe'),
  ];

  for (const toolPath of uvToolPaths) {
    if (fs.existsSync(toolPath)) {
      if (isDebug) {
        console.log(`[MoZuku] uv tool でインストールされた mozuku-lsp を検出: ${toolPath}`);
      }
      return {
        available: true,
        command: toolPath,
        args: []
      };
    }
  }

  // Also try PATH lookup
  try {
    const result = cp.spawnSync('mozuku-lsp', ['--version'], {
      timeout: 5000,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe']
    });

    if (result.status === 0 || result.error === undefined) {
      if (isDebug) {
        console.log('[MoZuku] グローバルにインストールされた mozuku-lsp を検出');
      }
      return {
        available: true,
        command: 'mozuku-lsp',
        args: []
      };
    }
  } catch {
    // Not available
  }

  // 3. Check system Python with mozuku_lsp module installed
  const pythonCommands = ['python3', 'python'];
  for (const pythonCmd of pythonCommands) {
    try {
      const result = cp.spawnSync(pythonCmd, ['-c', 'import mozuku_lsp'], {
        timeout: 5000,
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'pipe']
      });

      if (result.status === 0) {
        if (isDebug) {
          console.log(`[MoZuku] システムPythonに mozuku_lsp がインストールされています: ${pythonCmd}`);
        }
        return {
          available: true,
          command: pythonCmd,
          args: ['-m', 'mozuku_lsp.server']
        };
      }
    } catch {
      // Continue to next option
    }
  }

  if (isDebug) {
    console.log('[MoZuku] Python LSPサーバーが見つかりませんでした');
    console.log('[MoZuku] 検索したパス:', devPaths);
  }

  return {
    available: false,
    command: '',
    args: []
  };
}
