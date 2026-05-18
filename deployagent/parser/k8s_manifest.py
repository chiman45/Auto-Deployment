"""
Kubernetes manifest validator.
Adapted from new.py::analyze_yaml_static — checks apiVersion format,
resource limits, probe ports, and security contexts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ManifestIssue:
    level: str   # "error" | "warning" | "suggestion"
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.message}"


def validate_manifest(path: Path) -> list[ManifestIssue]:
    """
    Validate a Kubernetes manifest YAML file.
    Returns a list of ManifestIssue; empty list means clean.
    """
    issues: list[ManifestIssue] = []
    content = path.read_text(encoding="utf-8")

    try:
        docs = list(yaml.safe_load_all(content))
    except yaml.YAMLError as exc:
        issues.append(ManifestIssue("error", f"YAML parse failed: {exc}"))
        return issues

    # Line-by-line structural checks
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        if stripped == "selector" or (stripped.startswith("selector") and ":" not in stripped):
            issues.append(ManifestIssue("error", f"Line {i}: Missing colon after 'selector'"))

        if "port:" in line:
            after = line.split("port:", 1)[1].strip()
            if not after or not after[0].isdigit():
                issues.append(ManifestIssue("error", f"Line {i}: Missing or invalid port number after 'port:'"))

        if stripped.startswith("apiVersion") and ":" in stripped:
            val = stripped.split(":", 1)[1].strip()
            if " " in val:
                issues.append(ManifestIssue("error", f"Line {i}: apiVersion contains spaces — use '/' not ' /'"))

    # Document-level semantic checks
    for doc in docs:
        if not doc or not isinstance(doc, dict) or "kind" not in doc:
            continue

        if "apiVersion" in doc and " " in str(doc["apiVersion"]):
            issues.append(ManifestIssue("error", f"apiVersion '{doc['apiVersion']}' is invalid (contains spaces)"))

        kind = doc["kind"]

        if kind == "Deployment":
            _check_deployment(doc, issues)

        if kind in ("Deployment", "Pod", "DaemonSet", "StatefulSet"):
            pod_spec = doc.get("spec", {})
            if kind == "Deployment":
                pod_spec = doc["spec"].get("template", {}).get("spec", {})
            if "securityContext" not in pod_spec:
                issues.append(ManifestIssue("warning", f"{kind}: Missing pod-level securityContext"))

    return issues


def _check_deployment(doc: dict, issues: list[ManifestIssue]) -> None:
    pod_spec = doc.get("spec", {}).get("template", {}).get("spec", {})
    for container in pod_spec.get("containers", []):
        name = container.get("name", "unknown")

        if "resources" not in container:
            issues.append(ManifestIssue("warning", f"Container '{name}': missing resource limits"))
        else:
            res = container["resources"]
            if isinstance(res, dict) and "requests" in res:
                issues.append(ManifestIssue("suggestion", f"Container '{name}': resource requests defined"))

        if "securityContext" not in container:
            issues.append(ManifestIssue("suggestion", f"Container '{name}': consider adding securityContext"))

        if "livenessProbe" in container:
            probe = container["livenessProbe"]
            if isinstance(probe, dict) and "httpGet" in probe:
                if not probe["httpGet"].get("port"):
                    issues.append(ManifestIssue("error", f"Container '{name}': livenessProbe.httpGet missing port"))
