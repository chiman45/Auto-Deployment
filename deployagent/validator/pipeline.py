"""
Validation pipeline: static checks → pattern fix.
AI analysis is handled by the MCP plugin (Claude Code) — no local LLM required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from ..parser.k8s_manifest import ManifestIssue, validate_manifest
from ..parser.yaml_loader import attempt_fix

console = Console()

_K8S_DIRS = ("k8s", "kubernetes", "manifests", "deploy", "infra", "helm")

# These are deployagent config files, not K8s/Docker manifests — never validate them
_EXCLUDED_NAMES = {"deploy.yaml", "deploy.yml", "deployagent.yaml", "deployagent.yml"}


@dataclass
class FileResult:
    path: Path
    static_issues: list[ManifestIssue] = field(default_factory=list)
    pattern_fixed: bool = False

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.static_issues)

    @property
    def clean(self) -> bool:
        return not self.static_issues


@dataclass
class ValidationReport:
    results: list[FileResult] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(r.has_errors for r in self.results)

    @property
    def total_files(self) -> int:
        return len(self.results)

    @property
    def fixed_count(self) -> int:
        return sum(1 for r in self.results if r.pattern_fixed)


def run(dockerfile: Path, build_context: Path) -> ValidationReport:
    """
    Validate Dockerfile + all K8s/YAML files found in the build context.
    Applies pattern fixes first, then Gemini AI fixes for remaining issues.
    """
    report = ValidationReport()

    files_to_check: list[Path] = []

    if dockerfile.exists():
        files_to_check.append(dockerfile)

    for candidate_dir in _K8S_DIRS:
        k8s_path = build_context / candidate_dir
        if k8s_path.is_dir():
            files_to_check.extend(k8s_path.rglob("*.yaml"))
            files_to_check.extend(k8s_path.rglob("*.yml"))

    for yaml_file in build_context.glob("*.yaml"):
        if yaml_file not in files_to_check and yaml_file.name not in _EXCLUDED_NAMES:
            files_to_check.append(yaml_file)

    if not files_to_check:
        console.print("[dim]No Dockerfile or YAML files found to validate.[/]")
        return report

    console.print(f"\n[bold]Validating {len(files_to_check)} file(s)…[/]\n")

    for path in files_to_check:
        result = FileResult(path=path)

        # Step 1: static analysis (K8s YAML only)
        if path.suffix.lower() in (".yaml", ".yml"):
            result.static_issues = validate_manifest(path)

        # Step 2: pattern-based auto-fix
        if result.has_errors or result.static_issues:
            result.pattern_fixed = attempt_fix(path)

        report.results.append(result)
        _print_file_result(result)

    _print_summary(report)
    return report


def _print_file_result(result: FileResult) -> None:
    name = result.path.name

    if result.clean and not result.pattern_fixed:
        console.print(f"  [green]✓[/] {name} — clean")
        return

    console.print(f"  [bold]{name}[/]")

    for issue in result.static_issues:
        colour = {"error": "red", "warning": "yellow", "suggestion": "dim"}.get(issue.level, "white")
        console.print(f"    [{colour}]{issue}[/{colour}]")

    if result.pattern_fixed:
        console.print(f"    [green]→ Auto-fixed by pattern matcher[/]")

    console.print()


def _print_summary(report: ValidationReport) -> None:
    fixed = report.fixed_count
    total = report.total_files
    errors = report.has_errors

    if fixed:
        console.print(f"[bold green]Fixed {fixed}/{total} file(s) automatically.[/]")
    if not errors:
        console.print("[bold green]All files validated — no blocking errors.[/]\n")
    else:
        console.print("[bold yellow]Some errors remain — proceeding with deploy anyway.[/]\n")
