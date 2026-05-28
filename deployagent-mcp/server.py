"""
deployagent MCP server — Claude Code plugin for Dockerfile/K8s validation and deployment.

These tools READ files and return their content + structure to Claude Code.
Claude Code (the AI already running on your device) performs the analysis.
No external API key required.

Tools:
  - validate_dockerfile   : read a Dockerfile and return it for Claude to analyze
  - validate_k8s_manifest : read a K8s/YAML file and return it for Claude to analyze
  - pre_deploy_check      : scan all relevant files in a build context
  - apply_fix             : write a corrected file only when Claude explicitly instructs it
  - deploy                : run `deployagent apply <deploy.yaml> --yes`
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

_K8S_DIRS = ("k8s", "kubernetes", "manifests", "deploy", "infra", "helm")
_EXCLUDED_NAMES = {"deploy.yaml", "deploy.yml", "deployagent.yaml", "deployagent.yml"}

mcp = FastMCP("deployagent-validator")


@mcp.tool()
def validate_dockerfile(path: str) -> str:
    """
    Read a Dockerfile and return its content so Claude Code can analyze it
    for errors, security issues, and best-practice violations.
    Claude will report findings and suggest fixes without modifying the file.

    Args:
        path: Absolute or relative path to the Dockerfile.
    """
    p = Path(path).resolve()
    if not p.exists():
        return f"ERROR: File not found: {path}"

    content = p.read_text(encoding="utf-8")
    lines = content.splitlines()

    return (
        f"Dockerfile at: {p}\n"
        f"Lines: {len(lines)}\n"
        f"{'─' * 40}\n"
        f"{content}\n"
        f"{'─' * 40}\n"
        "Please analyze this Dockerfile for:\n"
        "1. Syntax errors\n"
        "2. Security vulnerabilities (running as root, exposed secrets, etc.)\n"
        "3. Best practice violations (layer caching, image size, multi-stage builds)\n"
        "4. Missing health checks or labels\n"
        "Report each issue with a concrete fix suggestion. Do NOT modify the file."
    )


@mcp.tool()
def validate_k8s_manifest(path: str) -> str:
    """
    Read a Kubernetes or Docker Compose YAML manifest and return its content
    so Claude Code can analyze it for misconfigurations and security issues.
    Claude will report findings and suggest fixes without modifying the file.

    Args:
        path: Absolute or relative path to the YAML file.
    """
    p = Path(path).resolve()
    if not p.exists():
        return f"ERROR: File not found: {path}"

    if p.name in _EXCLUDED_NAMES:
        return f"Skipped {p.name} — this is a deployagent config file, not a K8s manifest."

    content = p.read_text(encoding="utf-8")
    lines = content.splitlines()

    return (
        f"K8s/YAML manifest at: {p}\n"
        f"Lines: {len(lines)}\n"
        f"{'─' * 40}\n"
        f"{content}\n"
        f"{'─' * 40}\n"
        "Please analyze this manifest for:\n"
        "1. Missing required fields (apiVersion, kind, metadata, spec)\n"
        "2. Security issues (privileged containers, missing resource limits, etc.)\n"
        "3. Misconfigurations (wrong image references, port mismatches, etc.)\n"
        "4. Missing liveness/readiness probes\n"
        "Report each issue with a concrete fix suggestion. Do NOT modify the file."
    )


@mcp.tool()
def pre_deploy_check(build_context: str, dockerfile: str = "Dockerfile") -> str:
    """
    Scan a project's build context and return all Dockerfile and K8s YAML
    file contents so Claude Code can validate them before deployment.
    Claude will analyze each file and report any issues found.

    Args:
        build_context: Path to the project root / build context directory.
        dockerfile:    Relative path to the Dockerfile from build_context (default: Dockerfile).
    """
    ctx = Path(build_context).resolve()
    if not ctx.is_dir():
        return f"ERROR: build_context is not a directory: {build_context}"

    files_to_check: list[Path] = []

    df_path = ctx / dockerfile
    if df_path.exists():
        files_to_check.append(df_path)

    for d in _K8S_DIRS:
        k8s_dir = ctx / d
        if k8s_dir.is_dir():
            files_to_check.extend(k8s_dir.rglob("*.yaml"))
            files_to_check.extend(k8s_dir.rglob("*.yml"))

    for yaml_file in ctx.glob("*.yaml"):
        if yaml_file not in files_to_check and yaml_file.name not in _EXCLUDED_NAMES:
            files_to_check.append(yaml_file)
    for yaml_file in ctx.glob("*.yml"):
        if yaml_file not in files_to_check and yaml_file.name not in _EXCLUDED_NAMES:
            files_to_check.append(yaml_file)

    if not files_to_check:
        return "No Dockerfile or YAML files found to validate in the build context."

    sections: list[str] = [
        f"Pre-deploy scan of: {ctx}",
        f"Found {len(files_to_check)} file(s) to validate:",
        "",
    ]

    for p in files_to_check:
        name_lower = p.name.lower()
        is_dockerfile = name_lower == "dockerfile" or name_lower.startswith("dockerfile.")
        suffix = p.suffix.lower()

        content = p.read_text(encoding="utf-8")
        file_type = "Dockerfile" if is_dockerfile else "K8s/YAML manifest"

        sections.append(f"{'=' * 50}")
        sections.append(f"FILE: {p.name}  ({file_type})")
        sections.append(f"PATH: {p}")
        sections.append(f"{'─' * 50}")
        sections.append(content)
        sections.append("")

    sections.append("=" * 50)
    sections.append(
        "Please analyze ALL files above for errors, security issues, and misconfigurations. "
        "For each issue found, state the file name, describe the problem, and suggest a concrete fix. "
        "Do NOT modify any files."
    )

    return "\n".join(sections)


@mcp.tool()
def apply_fix(path: str, corrected_content: str) -> str:
    """
    Write corrected content to a file. Use this ONLY when the user has explicitly
    reviewed and approved the fix. Always show the diff to the user before calling this.

    Args:
        path:              Absolute or relative path to the file to fix.
        corrected_content: The complete corrected file content to write.
    """
    p = Path(path).resolve()
    if not p.exists():
        return f"ERROR: File not found: {path}. Will not create new files via this tool."

    original = p.read_text(encoding="utf-8")
    if original == corrected_content:
        return f"No changes needed — content is identical to current file."

    p.write_text(corrected_content, encoding="utf-8")
    return f"✓ Written to {p}. Original had {len(original.splitlines())} lines, new content has {len(corrected_content.splitlines())} lines."


@mcp.tool()
def deploy(deploy_yaml: str, skip_validate: bool = False) -> str:
    """
    Deploy a project by running: deployagent apply <deploy.yaml> --yes
    Streams the full output back so Claude can report progress and errors.

    Args:
        deploy_yaml:    Path to the deploy.yaml config file.
        skip_validate:  Set True to skip pre-deploy validation (default: False).
    """
    p = Path(deploy_yaml).resolve()
    if not p.exists():
        return f"ERROR: deploy.yaml not found: {deploy_yaml}"
    if not p.is_file():
        return f"ERROR: Not a file: {deploy_yaml}"

    cmd = ["deployagent", "apply", str(p), "--yes"]
    if skip_validate:
        cmd.append("--skip-validate")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(p.parent),
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return f"Deployment succeeded.\n\n{output}"
        else:
            return f"Deployment FAILED (exit code {result.returncode}).\n\n{output}"
    except subprocess.TimeoutExpired:
        return "ERROR: Deployment timed out after 10 minutes."
    except Exception as exc:
        return f"ERROR running deployagent: {exc}"


if __name__ == "__main__":
    mcp.run()
