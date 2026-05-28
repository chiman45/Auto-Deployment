"""Ollama-powered file analyzer and fixer (qwen2.5-coder:32b)."""
from __future__ import annotations

import urllib.request
import urllib.error
import json
from pathlib import Path

_MODEL = "codellama:latest"
_OLLAMA_URL = "http://localhost:11434/api/generate"


def _ask(prompt: str) -> str:
    payload = json.dumps({"model": _MODEL, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        _OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("response", "").strip()
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Ollama not reachable at {_OLLAMA_URL}. "
            "Make sure Ollama is running: `ollama serve`"
        ) from exc


def analyze(file_type: str, content: str) -> list[str]:
    """Ask the model to identify errors in a Dockerfile or K8s YAML."""
    prompt = (
        f"You are a DevOps expert. Analyze this {file_type} for errors, "
        f"security issues, and misconfigurations.\n"
        f"Return ONLY a plain numbered list of issues. "
        f"If there are no issues, reply with exactly: NO_ISSUES\n\n"
        f"```\n{content}\n```"
    )
    text = _ask(prompt)
    if text == "NO_ISSUES" or ("no issues" in text.lower() and len(text) < 50):
        return []
    lines = [l.strip() for l in text.splitlines() if l.strip() and l.strip()[0].isdigit()]
    return lines or [text]


def fix(file_type: str, content: str, issues: list[str]) -> str:
    """Ask the model to fix the issues. Returns corrected file content only."""
    issues_text = "\n".join(f"- {i}" for i in issues)
    prompt = (
        f"You are a DevOps expert. Fix ALL the following issues in this {file_type}.\n\n"
        f"Issues to fix:\n{issues_text}\n\n"
        f"Return ONLY the corrected file content with no explanation, "
        f"no markdown code fences, and no extra text.\n\n"
        f"Original file:\n{content}"
    )
    fixed = _ask(prompt)
    if fixed.startswith("```"):
        lines = fixed.splitlines()
        fixed = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    return fixed


def analyze_and_fix(path: Path) -> tuple[list[str], bool]:
    """Analyze a file with Ollama and report issues. Never modifies files."""
    suffix = path.suffix.lower()
    name = path.name.lower()

    if name == "dockerfile" or name.startswith("dockerfile."):
        file_type = "Dockerfile"
    elif suffix in (".yaml", ".yml"):
        file_type = "Kubernetes/Docker YAML"
    else:
        return [], False

    content = path.read_text(encoding="utf-8")
    issues = analyze(file_type, content)
    return issues, False
