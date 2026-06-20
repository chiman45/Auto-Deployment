"""
YAML config loader and validator.
Syntax checking logic adapted from new.py::check_yaml_syntax and fix_yaml_file.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class ImageConfig(BaseModel):
    repository: str
    tag: str = "latest"
    dockerfile: str = "./Dockerfile"
    build_context: str = "."


class ECSConfig(BaseModel):
    service_name: str
    task_family: str
    desired_count: int = 1
    container_name: str
    container_port: int = 8080
    cpu: int = 256
    memory: int = 512
    execution_role_arn: Optional[str] = None
    environment: dict[str, str] = {}   # injected as ECS container env vars


class ALBConfig(BaseModel):
    name: str
    target_group_name: str
    vpc_id: str
    subnets: list[str]
    security_groups: list[str]
    listener_port: int = 80
    internal: bool = False


class CloudFormationConfig(BaseModel):
    stack_name: str
    template_file: str
    parameters: dict[str, str] = {}


class HealthConfig(BaseModel):
    endpoint: str = "/health"
    timeout: int = 30
    retries: int = 5


class DeployConfig(BaseModel):
    service: str
    region: str = "us-east-1"
    cluster: str
    image: ImageConfig
    ecs: ECSConfig
    alb: Optional[ALBConfig] = None
    cloudformation: Optional[CloudFormationConfig] = None
    health: HealthConfig = HealthConfig()


def load_config(path: Path) -> tuple[DeployConfig, str]:
    """Load, validate, and hash a deploy config YAML. Returns (config, sha256_hash[:16])."""
    raw = path.read_text(encoding="utf-8")
    errors = _check_syntax(raw)
    if errors:
        raise ValueError("YAML syntax errors:\n" + "\n".join(f"  - {e}" for e in errors))
    data = yaml.safe_load(raw)
    config = DeployConfig(**data)
    file_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return config, file_hash


def _check_syntax(content: str) -> list[str]:
    """
    Static YAML syntax checker — adapted from new.py::check_yaml_syntax.
    Catches parse errors plus common structural issues (missing colons,
    incomplete port definitions, malformed apiVersion).
    """
    errors: list[str] = []

    try:
        list(yaml.safe_load_all(content))
    except yaml.YAMLError as exc:
        errors.append(f"YAML parse error: {exc}")
        return errors

    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue

        # Heuristic: key with no colon while the next line is more indented
        if ":" not in stripped and not stripped.startswith("|") and not stripped.startswith(">"):
            if i < len(lines):
                nxt = lines[i]
                cur_indent = len(line) - len(line.lstrip())
                nxt_indent = len(nxt) - len(nxt.lstrip()) if nxt.strip() else 0
                if nxt_indent > cur_indent:
                    errors.append(f"Line {i}: Possible missing colon after '{stripped}'")

        # Incomplete port definition
        if "port:" in stripped:
            after = stripped.split("port:", 1)[1].strip()
            if not after:
                errors.append(f"Line {i}: Incomplete port definition — missing port number")

        # apiVersion format (space instead of /)
        if stripped.startswith("apiVersion") and ":" in stripped:
            value = stripped.split(":", 1)[1].strip()
            if not value:
                errors.append(f"Line {i}: Missing apiVersion value")
            elif " " in value and "/" not in value:
                errors.append(f"Line {i}: Invalid apiVersion — space instead of '/' (e.g. 'apps/v1')")

    return errors


def attempt_fix(path: Path) -> bool:
    """
    Pattern-based YAML fixer — adapted from new.py::fix_yaml_file.
    Handles: missing colon after 'selector', space in apiVersion, empty probe port.
    Returns True if any fix was applied and written back.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines: list[str] = []
    fixed = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Fix: missing colon after bare 'selector'
        if stripped == "selector":
            indent = len(line) - len(line.lstrip())
            new_lines.append(" " * indent + "selector:\n")
            fixed = True
            continue

        # Fix: "apps /v1" → "apps/v1" in apiVersion
        if "apiVersion" in line and ":" in line:
            key, _, val = line.partition(":")
            if " /" in val:
                indent = len(line) - len(line.lstrip())
                fixed_val = val.replace(" /", "/")
                new_lines.append(" " * indent + f"apiVersion:{fixed_val}")
                fixed = True
                continue

        # Fix: probe port with no value — look up containerPort in surrounding lines
        if line.rstrip().endswith("port:"):
            ctx = "".join(lines[max(0, i - 5) : i])
            if "Probe" in ctx:
                port_found: str | None = None
                for j_line in lines[max(0, i - 10) : min(len(lines), i + 10)]:
                    if "containerPort:" in j_line:
                        candidate = j_line.split("containerPort:")[1].strip().rstrip()
                        if candidate.isdigit():
                            port_found = candidate
                            break
                if port_found:
                    indent = len(line) - len(line.lstrip())
                    new_lines.append(" " * indent + f"port: {port_found}\n")
                    fixed = True
                    continue

        new_lines.append(line)

    if fixed:
        path.write_text("".join(new_lines), encoding="utf-8")
    return fixed
