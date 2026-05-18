import * as vscode from 'vscode';z
import * as path from 'path';
import * as fs from 'fs';
import { exec, spawn } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

let outputChannel: vscode.OutputChannel;
let statusBarItem: vscode.StatusBarItem;
let analysisResults: any = null;

export function activate(context: vscode.ExtensionContext) {
    console.log('🚀 DevOps AI Code Reviewer is now active!');

    // Create output channel
    outputChannel = vscode.window.createOutputChannel('DevOps AI Reviewer');
    
    // Create status bar item
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'devops-code-reviewer.showResults';
    statusBarItem.text = '$(search) DevOps AI';
    statusBarItem.tooltip = 'Click to analyze repository';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('devops-code-reviewer.analyzeRepo', () => analyzeRepository(context)),
        vscode.commands.registerCommand('devops-code-reviewer.analyzeCurrentFile', () => analyzeCurrentFile()),
        vscode.commands.registerCommand('devops-code-reviewer.showResults', () => showResults(context)),
        vscode.commands.registerCommand('devops-code-reviewer.configure', () => configureSettings())
    );

    // Welcome message
    vscode.window.showInformationMessage('🤖 DevOps AI Code Reviewer ready! Click the status bar or run "DevOps AI: Analyze Repository"');
}

async function analyzeRepository(context: vscode.ExtensionContext) {
    outputChannel.clear();
    outputChannel.show();
    outputChannel.appendLine('🚀 DevOps AI Code Reviewer - Starting Analysis');
    outputChannel.appendLine('='.repeat(60));

    try {
        // Check Ollama
        const ollamaRunning = await checkOllama();
        if (!ollamaRunning) {
            const install = await vscode.window.showErrorMessage(
                '❌ Ollama is not running! Install and start Ollama to use local AI models.',
                'Install Ollama',
                'Cancel'
            );
            if (install === 'Install Ollama') {
                vscode.env.openExternal(vscode.Uri.parse('https://ollama.ai'));
            }
            return;
        }

        // Get configuration
        const config = vscode.workspace.getConfiguration('devopsReviewer');
        let githubToken = config.get<string>('githubToken') || '';
        
        // Prompt for GitHub token if not set
        if (!githubToken) {
            githubToken = await vscode.window.showInputBox({
                prompt: '🔑 Enter your GitHub Personal Access Token',
                password: true,
                placeHolder: 'ghp_xxxxxxxxxxxxxxxxxxxx',
                ignoreFocusOut: true
            }) || '';
            
            if (!githubToken) {
                vscode.window.showErrorMessage('❌ GitHub token is required!');
                return;
            }

            // Ask to save token
            const save = await vscode.window.showQuickPick(['Yes', 'No'], {
                placeHolder: 'Save token to settings? (Recommended)'
            });
            
            if (save === 'Yes') {
                await config.update('githubToken', githubToken, vscode.ConfigurationTarget.Global);
            }
        }

        // Get repository URL
        const repoUrl = await vscode.window.showInputBox({
            prompt: '🔗 Enter GitHub repository URL',
            placeHolder: 'https://github.com/username/repository',
            value: 'https://github.com/chiman45/docker',
            ignoreFocusOut: true
        });

        if (!repoUrl) {
            vscode.window.showWarningMessage('Analysis cancelled');
            return;
        }

        // Update status bar
        statusBarItem.text = '$(sync~spin) Analyzing...';
        statusBarItem.tooltip = 'Analysis in progress...';

        // Run Python analyzer with progress
        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: '🤖 DevOps AI Analysis',
            cancellable: false
        }, async (progress) => {
            progress.report({ increment: 0, message: 'Checking Ollama models...' });
            
            const scriptPath = getScriptPath();
            const pythonPath = config.get<string>('pythonPath') || 'python';

            outputChannel.appendLine(`\n📝 Script: ${scriptPath}`);
            outputChannel.appendLine(`🐍 Python: ${pythonPath}`);
            outputChannel.appendLine(`🔗 Repository: ${repoUrl}\n`);

            progress.report({ increment: 10, message: 'Cloning repository...' });

            // Execute Python script with piped inputs
            const result = await executePythonScript(
                pythonPath,
                scriptPath,
                githubToken,
                repoUrl,
                outputChannel,
                progress
            );

            if (result.success) {
                statusBarItem.text = '$(check) Analysis Complete';
                statusBarItem.tooltip = 'Click to view results';
                analysisResults = result;
                
                // Auto-show results
                await showResults(context);
                
                vscode.window.showInformationMessage('✅ Analysis completed! Check the results panel.');
            } else {
                statusBarItem.text = '$(error) Analysis Failed';
                statusBarItem.tooltip = 'Analysis failed. Check output.';
                vscode.window.showErrorMessage(`❌ Analysis failed: ${result.error}`);
            }
        });

    } catch (error) {
        outputChannel.appendLine(`\n❌ Error: ${error}`);
        statusBarItem.text = '$(error) DevOps AI';
        vscode.window.showErrorMessage(`Analysis error: ${error}`);
    }
}

async function analyzeCurrentFile() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('No file is currently open');
        return;
    }

    const filePath = editor.document.uri.fsPath;
    const fileName = path.basename(filePath);
    const ext = path.extname(filePath).toLowerCase();

    // Check if it's a supported file type
    if (!['.yml', '.yaml', '.dockerfile'].includes(ext) && !fileName.toLowerCase().includes('dockerfile')) {
        vscode.window.showWarningMessage('This file type is not supported. Only YAML and Dockerfile files can be analyzed.');
        return;
    }

    outputChannel.clear();
    outputChannel.show();
    outputChannel.appendLine(`🔍 Analyzing: ${fileName}`);
    outputChannel.appendLine('='.repeat(60));

    const content = editor.document.getText();
    const fileType = ext === '.yml' || ext === '.yaml' ? 'yaml' : 'docker';

    // Simple static analysis
    const issues = performStaticAnalysis(content, fileType, fileName);
    
    if (issues.length === 0) {
        outputChannel.appendLine('✅ No issues found!');
        vscode.window.showInformationMessage(`✅ ${fileName} looks good!`);
    } else {
        outputChannel.appendLine(`\n⚠️ Found ${issues.length} issue(s):\n`);
        issues.forEach((issue, idx) => {
            outputChannel.appendLine(`${idx + 1}. ${issue}`);
        });
        
        vscode.window.showWarningMessage(`⚠️ Found ${issues.length} issue(s) in ${fileName}. Check output.`);
    }
}

async function showResults(context: vscode.ExtensionContext) {
    // Create and show WebView panel
    const panel = vscode.window.createWebviewPanel(
        'devopsResults',
        '🤖 DevOps AI Analysis Results',
        vscode.ViewColumn.One,
        {
            enableScripts: true,
            retainContextWhenHidden: true
        }
    );

    panel.webview.html = getResultsHtml(analysisResults);
}

async function configureSettings() {
    const config = vscode.workspace.getConfiguration('devopsReviewer');
    
    const options = [
        { label: '$(key) Set GitHub Token', description: 'Configure GitHub Personal Access Token' },
        { label: '$(tools) Set Python Path', description: 'Configure Python executable path' },
        { label: '$(file-code) Set Script Path', description: 'Configure analyzer script location' },
        { label: '$(server) Set Ollama URL', description: 'Configure Ollama API endpoint' },
        { label: '$(settings-gear) Auto-fix Settings', description: 'Configure automatic fixing behavior' }
    ];

    const selection = await vscode.window.showQuickPick(options, {
        placeHolder: 'Select setting to configure'
    });

    if (!selection) {
        return;
    }

    if (selection.label.includes('GitHub Token')) {
        const token = await vscode.window.showInputBox({
            prompt: 'Enter GitHub Personal Access Token',
            password: true,
            value: config.get<string>('githubToken')
        });
        if (token !== undefined) {
            await config.update('githubToken', token, vscode.ConfigurationTarget.Global);
            vscode.window.showInformationMessage('✅ GitHub token updated');
        }
    } else if (selection.label.includes('Python Path')) {
        const pythonPath = await vscode.window.showInputBox({
            prompt: 'Enter Python executable path',
            value: config.get<string>('pythonPath')
        });
        if (pythonPath) {
            await config.update('pythonPath', pythonPath, vscode.ConfigurationTarget.Global);
            vscode.window.showInformationMessage('✅ Python path updated');
        }
    } else if (selection.label.includes('Script Path')) {
        const scriptPath = await vscode.window.showInputBox({
            prompt: 'Enter analyzer script path',
            value: config.get<string>('scriptPath')
        });
        if (scriptPath) {
            await config.update('scriptPath', scriptPath, vscode.ConfigurationTarget.Global);
            vscode.window.showInformationMessage('✅ Script path updated');
        }
    } else if (selection.label.includes('Ollama URL')) {
        const ollamaUrl = await vscode.window.showInputBox({
            prompt: 'Enter Ollama API URL',
            value: config.get<string>('ollamaUrl')
        });
        if (ollamaUrl) {
            await config.update('ollamaUrl', ollamaUrl, vscode.ConfigurationTarget.Global);
            vscode.window.showInformationMessage('✅ Ollama URL updated');
        }
    } else if (selection.label.includes('Auto-fix')) {
        const autoFix = await vscode.window.showQuickPick(['Enable', 'Disable'], {
            placeHolder: 'Auto-fix detected issues?'
        });
        if (autoFix) {
            await config.update('autoFix', autoFix === 'Enable', vscode.ConfigurationTarget.Global);
            vscode.window.showInformationMessage(`✅ Auto-fix ${autoFix.toLowerCase()}d`);
        }
    }
}

async function checkOllama(): Promise<boolean> {
    try {
        const config = vscode.workspace.getConfiguration('devopsReviewer');
        const ollamaUrl = config.get<string>('ollamaUrl') || 'http://localhost:11434';
        
        const response = await fetch(`${ollamaUrl}/api/tags`, { 
            signal: AbortSignal.timeout(5000) 
        });
        
        if (response.ok) {
            const data: any = await response.json();
            const models = data.models || [];
            outputChannel.appendLine(`✅ Ollama running with ${models.length} model(s)`);
            
            if (models.length === 0) {
                vscode.window.showWarningMessage('⚠️ No Ollama models found. Run: ollama pull llama3 && ollama pull codellama');
            }
            
            return true;
        }
        return false;
    } catch (error) {
        outputChannel.appendLine(`❌ Ollama check failed: ${error}`);
        return false;
    }
}

function getScriptPath(): string {
    const config = vscode.workspace.getConfiguration('devopsReviewer');
    let scriptPath = config.get<string>('scriptPath') || '';
    
    if (!scriptPath || scriptPath.includes('${workspaceFolder}')) {
        // Default to new.py in parent directory
        const workspaceFolder = vscode.workspace.workspaceFolders?.[0].uri.fsPath;
        if (workspaceFolder) {
            scriptPath = path.join(workspaceFolder, '..', 'new.py');
        }
    }
    
    return scriptPath;
}

async function executePythonScript(
    pythonPath: string,
    scriptPath: string,
    githubToken: string,
    repoUrl: string,
    output: vscode.OutputChannel,
    progress: vscode.Progress<{ message?: string; increment?: number }>
): Promise<any> {
    return new Promise((resolve) => {
        const pythonProcess = spawn(pythonPath, [scriptPath], {
            cwd: path.dirname(scriptPath)
        });

        let currentStep = 20;
        let stdout = '';
        let stderr = '';

        // Send inputs to Python script
        pythonProcess.stdin.write(`${githubToken}\n`);
        pythonProcess.stdin.write(`${repoUrl}\n`);

        pythonProcess.stdout.on('data', (data) => {
            const text = data.toString();
            stdout += text;
            output.append(text);

            // Update progress based on output
            if (text.includes('Cloning')) {
                progress.report({ increment: 10, message: 'Cloning repository...' });
            } else if (text.includes('Building RAG')) {
                progress.report({ increment: 15, message: 'Building RAG index...' });
            } else if (text.includes('Analyzing')) {
                progress.report({ increment: 20, message: 'Analyzing files...' });
            } else if (text.includes('Fixing')) {
                progress.report({ increment: 15, message: 'Fixing issues...' });
            } else if (text.includes('Docker')) {
                // Auto-answer Docker build question
                const config = vscode.workspace.getConfiguration('devopsReviewer');
                const autoBuild = config.get<boolean>('autoBuildDocker');
                
                if (text.includes('build and run Docker')) {
                    pythonProcess.stdin.write(autoBuild ? 'yes\n' : 'no\n');
                } else if (text.includes('stop the containers')) {
                    pythonProcess.stdin.write('no\n');
                }
                
                progress.report({ increment: 10, message: 'Docker operations...' });
            } else if (text.includes('branch name')) {
                // Auto-provide branch name
                pythonProcess.stdin.write('ai-analysis-fixes\n');
                progress.report({ increment: 5, message: 'Creating branch...' });
            } else if (text.includes('commit message')) {
                // Auto-provide commit message
                pythonProcess.stdin.write('🤖 AI-powered DevOps analysis and fixes\n');
                progress.report({ increment: 5, message: 'Committing changes...' });
            }
        });

        pythonProcess.stderr.on('data', (data) => {
            const text = data.toString();
            stderr += text;
            output.append(`[ERROR] ${text}`);
        });

        pythonProcess.on('close', (code) => {
            progress.report({ increment: 100, message: 'Complete!' });
            
            if (code === 0) {
                resolve({
                    success: true,
                    stdout,
                    stderr
                });
            } else {
                resolve({
                    success: false,
                    error: `Process exited with code ${code}`,
                    stdout,
                    stderr
                });
            }
        });

        pythonProcess.on('error', (error) => {
            resolve({
                success: false,
                error: error.message
            });
        });
    });
}

function performStaticAnalysis(content: string, fileType: string, fileName: string): string[] {
    const issues: string[] = [];
    const lines = content.split('\n');

    if (fileType === 'yaml') {
        // YAML checks
        lines.forEach((line, idx) => {
            if (line.trim() && !line.trim().startsWith('#')) {
                // Check for missing colons
                if (line.includes(':') === false && line.trim().length > 0 && !line.trim().startsWith('-')) {
                    issues.push(`Line ${idx + 1}: Missing colon (:) in key-value pair`);
                }
                
                // Check for tab characters
                if (line.includes('\t')) {
                    issues.push(`Line ${idx + 1}: Use spaces instead of tabs for indentation`);
                }
            }
        });

        // Check for common K8s issues
        if (!content.includes('apiVersion:')) {
            issues.push('Missing required field: apiVersion');
        }
        if (fileName.includes('service') && !content.includes('kind: Service')) {
            issues.push('Service file missing "kind: Service"');
        }
    } else if (fileType === 'docker') {
        // Dockerfile checks
        if (!content.toUpperCase().includes('FROM ')) {
            issues.push('Missing FROM instruction');
        }
        
        lines.forEach((line, idx) => {
            const trimmed = line.trim();
            if (trimmed.startsWith('RUN apt-get') && !trimmed.includes('-y')) {
                issues.push(`Line ${idx + 1}: Missing -y flag in apt-get command`);
            }
            
            if (trimmed.startsWith('FROM') && trimmed.includes(':latest')) {
                issues.push(`Line ${idx + 1}: Avoid using :latest tag, specify exact version`);
            }
        });
    }

    return issues;
}

function getResultsHtml(results: any): string {
    const hasResults = results && results.success;
    
    return `<!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>DevOps AI Results</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                padding: 20px;
                background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
                color: #fff;
                min-height: 100vh;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            .header {
                text-align: center;
                padding: 40px 0;
                border-bottom: 2px solid rgba(255,255,255,0.2);
                margin-bottom: 40px;
            }
            .header h1 {
                font-size: 2.5em;
                margin-bottom: 10px;
                background: linear-gradient(to right, #60a5fa, #a78bfa);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }
            .header p {
                font-size: 1.2em;
                opacity: 0.9;
            }
            .card {
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 15px;
                padding: 30px;
                margin-bottom: 25px;
                border: 1px solid rgba(255, 255, 255, 0.2);
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            }
            .card h2 {
                color: #60a5fa;
                margin-bottom: 20px;
                font-size: 1.8em;
            }
            .status {
                display: inline-block;
                padding: 8px 16px;
                border-radius: 20px;
                font-weight: 600;
                margin: 5px;
            }
            .status.success { background: #10b981; }
            .status.warning { background: #f59e0b; }
            .status.error { background: #ef4444; }
            .empty-state {
                text-align: center;
                padding: 60px 20px;
            }
            .empty-state h2 {
                font-size: 2em;
                margin-bottom: 15px;
                opacity: 0.8;
            }
            .empty-state p {
                font-size: 1.1em;
                opacity: 0.7;
                margin-bottom: 30px;
            }
            .cta-button {
                display: inline-block;
                background: linear-gradient(to right, #60a5fa, #a78bfa);
                color: white;
                padding: 15px 40px;
                border-radius: 30px;
                text-decoration: none;
                font-weight: 600;
                font-size: 1.1em;
                transition: transform 0.2s;
            }
            .cta-button:hover {
                transform: translateY(-2px);
            }
            .feature-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-top: 30px;
            }
            .feature {
                background: rgba(255, 255, 255, 0.05);
                padding: 20px;
                border-radius: 10px;
                text-align: center;
            }
            .feature-icon {
                font-size: 3em;
                margin-bottom: 10px;
            }
            .feature h3 {
                margin-bottom: 10px;
                color: #60a5fa;
            }
            pre {
                background: rgba(0, 0, 0, 0.3);
                padding: 15px;
                border-radius: 8px;
                overflow-x: auto;
                margin: 10px 0;
            }
            .metric {
                display: flex;
                justify-content: space-between;
                padding: 10px 0;
                border-bottom: 1px solid rgba(255,255,255,0.1);
            }
            .metric:last-child {
                border-bottom: none;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 DevOps AI Code Reviewer</h1>
                <p>Powered by Ollama Local Models</p>
            </div>

            ${hasResults ? `
                <div class="card">
                    <h2>📊 Analysis Summary</h2>
                    <div class="metric">
                        <span>Status:</span>
                        <span class="status success">✅ Completed</span>
                    </div>
                    <div class="metric">
                        <span>Files Analyzed:</span>
                        <strong>Multiple</strong>
                    </div>
                    <div class="metric">
                        <span>Issues Found:</span>
                        <strong>Check Output Channel</strong>
                    </div>
                </div>

                <div class="card">
                    <h2>🛠️ Actions Taken</h2>
                    <ul style="list-style: none; padding-left: 0;">
                        <li style="padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.1);">
                            ✅ Repository cloned and analyzed
                        </li>
                        <li style="padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.1);">
                            ✅ RAG index built
                        </li>
                        <li style="padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.1);">
                            ✅ Files scanned for issues
                        </li>
                        <li style="padding: 10px 0;">
                            ✅ Fixes applied and committed
                        </li>
                    </ul>
                </div>
            ` : `
                <div class="empty-state">
                    <div class="feature-icon">🚀</div>
                    <h2>Ready to Analyze Your Code!</h2>
                    <p>Run the analyzer to see results here</p>
                    <div class="feature-grid">
                        <div class="feature">
                            <div class="feature-icon">🐳</div>
                            <h3>Docker Analysis</h3>
                            <p>Detect Dockerfile issues and security problems</p>
                        </div>
                        <div class="feature">
                            <div class="feature-icon">☸️</div>
                            <h3>Kubernetes</h3>
                            <p>Validate K8s manifests and configs</p>
                        </div>
                        <div class="feature">
                            <div class="feature-icon">📝</div>
                            <h3>YAML Linting</h3>
                            <p>Fix syntax and formatting errors</p>
                        </div>
                        <div class="feature">
                            <div class="feature-icon">🤖</div>
                            <h3>AI-Powered</h3>
                            <p>Using local Ollama models (codellama, llama3)</p>
                        </div>
                    </div>
                </div>
            `}

            <div class="card">
                <h2>💡 Quick Tips</h2>
                <ul style="line-height: 1.8; padding-left: 20px;">
                    <li>Make sure Ollama is running: <code>ollama serve</code></li>
                    <li>Install models: <code>ollama pull llama3 && ollama pull codellama</code></li>
                    <li>Check the Output Channel for detailed logs</li>
                    <li>Configure settings via Command Palette: "DevOps AI: Configure Settings"</li>
                </ul>
            </div>
        </div>
    </body>
    </html>`;
}

export function deactivate() {
    if (outputChannel) {
        outputChannel.dispose();
    }
    if (statusBarItem) {
        statusBarItem.dispose();
    }
}
