# 🤖 DevOps AI Code Reviewer

AI-powered VS Code extension that automatically detects and fixes issues in Docker, Kubernetes, and YAML files using local Ollama models.

## ✨ Features

- **🐳 Docker Analysis** - Detects Dockerfile syntax errors, security issues, and best practice violations
- **☸️ Kubernetes Validation** - Validates K8s manifests for missing fields, incorrect apiVersions, and port issues
- **📝 YAML Linting** - Fixes syntax errors, indentation problems, and formatting issues
- **🤖 AI-Powered Fixes** - Uses local Ollama models (codellama, llama3) for intelligent code analysis
- **🔒 Privacy-First** - All processing happens locally with Ollama - no cloud API needed
- **🚀 Auto-Commit** - Automatically commits fixes to a new branch and pushes to GitHub
- **📊 Beautiful Results** - Interactive WebView panel with analysis results
- **⚡ Fast & Free** - No rate limits, no API costs, completely free

## 🚀 Quick Start

### Prerequisites

1. **Install Ollama** - Download from [ollama.ai](https://ollama.ai)
2. **Pull AI Models**:
   ```bash
   ollama pull llama3
   ollama pull codellama
   ```
3. **Start Ollama**:
   ```bash
   ollama serve
   ```

### Installation

1. Open VS Code
2. Press `Ctrl+Shift+P` and type "Install Extensions"
3. Search for "DevOps AI Code Reviewer"
4. Click Install

## 🎯 Usage

### Analyze Repository

1. Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (Mac)
2. Type "DevOps AI: Analyze Repository"
3. Enter your GitHub Personal Access Token
4. Enter the repository URL
5. Watch the magic happen! ✨

### Analyze Current File

1. Open a YAML or Dockerfile
2. Right-click in the editor
3. Select "DevOps AI: Analyze Current File"
4. View issues in the Output channel

## ⚙️ Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `devopsReviewer.pythonPath` | `python` | Path to Python executable |
| `devopsReviewer.scriptPath` | `${workspaceFolder}/../new.py` | Path to analyzer script |
| `devopsReviewer.githubToken` | `` | GitHub Personal Access Token |
| `devopsReviewer.ollamaUrl` | `http://localhost:11434` | Ollama API endpoint |
| `devopsReviewer.autoFix` | `true` | Automatically fix issues |
| `devopsReviewer.autoBuildDocker` | `false` | Auto-build Docker containers |

## 🛠️ Commands

- `DevOps AI: Analyze Repository` - Analyze entire GitHub repository
- `DevOps AI: Analyze Current File` - Quick analysis of open file
- `DevOps AI: Show Results` - Display analysis results
- `DevOps AI: Configure Settings` - Interactive settings wizard

## 📋 What It Detects

### Docker
✅ Missing FROM statements  
✅ Security vulnerabilities  
✅ Best practice violations  

### Kubernetes
✅ Missing apiVersion/kind  
✅ Invalid resource specs  
✅ Port configuration errors  

### YAML
✅ Syntax errors  
✅ Indentation issues  
✅ Tab characters  

## 🐛 Troubleshooting

**Ollama not running?**
```bash
ollama serve
```

**Missing models?**
```bash
ollama pull llama3
ollama pull codellama
```

## 📝 License

MIT License

## 🔗 Links

- [GitHub Repository](https://github.com/chiman45/devops-code-reviewer)
- [Ollama Documentation](https://ollama.ai)

---

**Made with ❤️ for DevOps engineers**
