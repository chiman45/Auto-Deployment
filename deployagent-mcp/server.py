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
  - get_deploy_logs       : tail live deployment output
  - get_ecs_diagnostics   : pull ECS events + stopped-task reasons + CloudWatch logs for auto-diagnosis
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError
from mcp.server.fastmcp import FastMCP

# Tracks running deployments: deploy_yaml path → {proc, log_path, start_time}
_active_deploys: dict[str, dict] = {}

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


def _stream_to_file(proc: subprocess.Popen, log_path: Path) -> None:
    """Background thread: writes stdout+stderr lines to log file in real time."""
    with open(log_path, "w", encoding="utf-8") as f:
        for line in proc.stdout:  # type: ignore[union-attr]
            f.write(line)
            f.flush()
    proc.wait()


@mcp.tool()
def deploy(deploy_yaml: str, skip_validate: bool = False) -> str:
    """
    Start a deployment in the background and return immediately.
    Output is streamed to a log file in real time.
    Use get_deploy_logs() to check progress while it runs.

    Args:
        deploy_yaml:   Path to the deploy.yaml config file.
        skip_validate: Set True to skip pre-deploy validation (default: False).
    """
    p = Path(deploy_yaml).resolve()
    if not p.exists():
        return f"ERROR: deploy.yaml not found: {deploy_yaml}"
    if not p.is_file():
        return f"ERROR: Not a file: {deploy_yaml}"

    key = str(p)
    if key in _active_deploys and _active_deploys[key]["proc"].poll() is None:
        log_path = _active_deploys[key]["log_path"]
        return f"Deployment already running. Use get_deploy_logs to check progress.\nLog: {log_path}"

    cmd = [sys.executable, "-m", "deployagent", "apply", key, "--yes"]
    if skip_validate:
        cmd.append("--skip-validate")

    log_path = Path(tempfile.gettempdir()) / f"deployagent_{p.stem}_{int(time.time())}.log"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={
                **__import__("os").environ,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "NO_COLOR": "1",
                "TERM": "dumb",
            },
            cwd=str(p.parent),
        )
    except Exception as exc:
        return f"ERROR starting deployment: {exc}"

    _active_deploys[key] = {"proc": proc, "log_path": log_path, "start_time": time.time()}

    t = threading.Thread(target=_stream_to_file, args=(proc, log_path), daemon=True)
    t.start()

    return (
        f"Deployment started (PID {proc.pid}).\n"
        f"Log file: {log_path}\n\n"
        f"Call get_deploy_logs(\"{deploy_yaml}\") every 10-15 seconds to see live progress."
    )


@mcp.tool()
def get_deploy_logs(deploy_yaml: str, last_n_lines: int = 30) -> str:
    """
    Read the live deployment log to check progress.
    Call this repeatedly while a deployment is running.

    Args:
        deploy_yaml:  Same path you passed to deploy().
        last_n_lines: How many recent lines to return (default 30).
    """
    key = str(Path(deploy_yaml).resolve())

    if key not in _active_deploys:
        return "No active or recent deployment found for this path. Run deploy() first."

    entry = _active_deploys[key]
    proc: subprocess.Popen = entry["proc"]
    log_path: Path = entry["log_path"]
    elapsed = int(time.time() - entry["start_time"])

    status = "RUNNING" if proc.poll() is None else (
        "SUCCEEDED" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
    )

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []

    tail = "\n".join(lines[-last_n_lines:]) if lines else "(no output yet)"

    return (
        f"Status : {status}\n"
        f"Elapsed: {elapsed}s\n"
        f"Log    : {log_path}\n"
        f"Lines  : {len(lines)} total, showing last {min(last_n_lines, len(lines))}\n"
        f"{'─' * 50}\n"
        f"{tail}"
    )


@mcp.tool()
def get_service_logs(deploy_yaml: str, lines: int = 100, minutes: int = 30, stream: str = "") -> str:
    """
    Tail CloudWatch logs for the deployed ECS service in one command.
    Reads the project's deploy.yaml to find the log group automatically.

    Args:
        deploy_yaml: Path to the deploy.yaml config file.
        lines:       Max log lines to return per stream (default 100).
        minutes:     How far back to look in minutes (default 30).
        stream:      Specific log stream name to read (default: latest 3 streams).
    """
    import yaml as _yaml
    p = Path(deploy_yaml).resolve()
    if not p.exists():
        return f"ERROR: deploy.yaml not found: {deploy_yaml}"
    try:
        cfg = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except _yaml.YAMLError as exc:
        return f"ERROR parsing deploy.yaml: {exc}"

    region      = cfg.get("region", "us-east-1")
    task_family = (cfg.get("ecs") or {}).get("task_family", "")
    service     = (cfg.get("ecs") or {}).get("service_name", "")
    log_group   = f"/ecs/{task_family}"
    start_ms    = int((time.time() - minutes * 60) * 1000)

    cw = boto3.client("logs", region_name=region)

    out: list[str] = [
        f"CloudWatch Logs",
        f"  Service    : {service}",
        f"  Log group  : {log_group}",
        f"  Looking back: last {minutes} min",
        f"  Fetched at : {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
    ]

    try:
        if stream:
            streams = [{"logStreamName": stream}]
        else:
            resp = cw.describe_log_streams(
                logGroupName=log_group,
                orderBy="LastEventTime",
                descending=True,
                limit=3,
            )
            streams = resp.get("logStreams", [])

        if not streams:
            out.append(f"No log streams found in {log_group}.")
            out.append("The service may not have started yet, or the log group doesn't exist.")
            return "\n".join(out)

        for s in streams:
            sname = s["logStreamName"] if isinstance(s, dict) else s
            out.append(f"{'─' * 60}")
            out.append(f"Stream: {sname}")
            out.append(f"{'─' * 60}")
            events_resp = cw.get_log_events(
                logGroupName=log_group,
                logStreamName=sname,
                startTime=start_ms,
                limit=lines,
                startFromHead=False,
            )
            events = events_resp.get("events", [])
            if not events:
                out.append("  (no log events in this time window)")
            else:
                for ev in events:
                    ts = ev.get("timestamp", 0)
                    ts_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M:%S")
                    out.append(f"  [{ts_str}] {ev.get('message', '').rstrip()}")
            out.append("")

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            out.append(f"Log group '{log_group}' does not exist.")
            out.append("The task has never run or has not written logs yet.")
        else:
            out.append(f"AWS error: {exc}")

    return "\n".join(out)


@mcp.tool()
def prepare_deploy(deploy_yaml: str) -> str:
    """
    ALWAYS call this before deploy(). It:
      1. Reads the current deploy.yaml and shows the user what is configured.
      2. Checks AWS for what already exists (service, ALB, task definition).
      3. Returns a structured list of questions Claude must ask the user before proceeding.

    Claude should present the current values, ask the user to confirm or change each one,
    then call update_deploy_config() with any changes, then call deploy().

    Args:
        deploy_yaml: Path to the deploy.yaml config file.
    """
    import yaml as _yaml

    p = Path(deploy_yaml).resolve()
    existing_content: str | None = None
    cfg: dict = {}

    if p.exists():
        try:
            existing_content = p.read_text(encoding="utf-8")
            cfg = _yaml.safe_load(existing_content) or {}
        except _yaml.YAMLError as exc:
            return f"ERROR parsing deploy.yaml: {exc}"

    ecs_cfg   = cfg.get("ecs") or {}
    image_cfg = cfg.get("image") or {}
    alb_cfg   = cfg.get("alb") or {}

    region       = cfg.get("region", "us-east-1")
    cluster      = cfg.get("cluster", "")
    service_name = ecs_cfg.get("service_name", "")
    task_family  = ecs_cfg.get("task_family", "")
    container    = ecs_cfg.get("container_name", "")
    port         = ecs_cfg.get("container_port", "")
    cpu          = ecs_cfg.get("cpu", 256)
    memory       = ecs_cfg.get("memory", 512)
    count        = ecs_cfg.get("desired_count", 1)
    repo         = image_cfg.get("repository", "")
    tag          = image_cfg.get("tag", "latest")
    dockerfile   = image_cfg.get("dockerfile", "./Dockerfile")
    alb_name     = alb_cfg.get("name", "")
    env_vars     = ecs_cfg.get("environment") or {}

    # ── Check AWS for existing resources ──────────────────────────────────────
    aws_status: list[str] = []
    service_exists = False
    td_revision: str | None = None

    try:
        ecs_client = boto3.client("ecs", region_name=region)

        if task_family:
            try:
                td_resp = ecs_client.describe_task_definition(taskDefinition=task_family)
                td_revision = str(td_resp["taskDefinition"].get("revision", "?"))
                aws_status.append(f"  Task definition : {task_family}:{td_revision}  [EXISTS]")
            except ClientError:
                aws_status.append(f"  Task definition : {task_family}  [NOT FOUND — will create]")

        if cluster and service_name:
            try:
                svc_resp = ecs_client.describe_services(cluster=cluster, services=[service_name])
                svcs = svc_resp.get("services", [])
                if svcs and svcs[0]["status"] != "INACTIVE":
                    svc = svcs[0]
                    running = svc.get("runningCount", 0)
                    desired = svc.get("desiredCount", 0)
                    service_exists = True
                    aws_status.append(
                        f"  ECS service     : {service_name}  [EXISTS — {running}/{desired} tasks running]"
                    )
                    aws_status.append(
                        "                    ⚠ Service already has tasks running. "
                        "Redeploying will do a rolling update."
                    )
                    lbs = svc.get("loadBalancers", [])
                    if lbs:
                        aws_status.append(f"                    Load balancer attached: YES")
                    else:
                        aws_status.append(
                            "                    Load balancer attached: NO — "
                            "to add an ALB, service must be deleted and recreated."
                        )
                else:
                    aws_status.append(f"  ECS service     : {service_name}  [NOT FOUND — will create]")
            except ClientError:
                aws_status.append(f"  ECS service     : {service_name}  [NOT FOUND — will create]")

        if alb_name:
            try:
                alb_client = boto3.client("elbv2", region_name=region)
                alb_resp = alb_client.describe_load_balancers(Names=[alb_name])
                lbs = alb_resp.get("LoadBalancers", [])
                if lbs:
                    dns = lbs[0].get("DNSName", "?")
                    aws_status.append(f"  ALB             : {alb_name}  [EXISTS — {dns}]")
                else:
                    aws_status.append(f"  ALB             : {alb_name}  [NOT FOUND — will create]")
            except ClientError:
                aws_status.append(f"  ALB             : {alb_name}  [NOT FOUND — will create]")

    except Exception as exc:
        aws_status.append(f"  (AWS lookup error: {exc})")

    # ── Build output ──────────────────────────────────────────────────────────
    out: list[str] = [
        "PRE-DEPLOY CONFIGURATION REVIEW",
        f"Config file: {p}",
        "",
        "── Current deploy.yaml values ───────────────────────────────",
        f"  service          : {cfg.get('service', '(not set)')}",
        f"  region           : {region}",
        f"  cluster          : {cluster or '(not set)'}",
        f"  container_name   : {container or '(not set)'}",
        f"  container_port   : {port or '(not set)'}",
        f"  cpu / memory     : {cpu} / {memory}",
        f"  desired_count    : {count}",
        f"  image.repository : {repo or '(not set)'}",
        f"  image.tag        : {tag}",
        f"  dockerfile       : {dockerfile}",
        f"  alb.name         : {alb_name or '(not set)'}",
        f"  environment vars : {len(env_vars)} set"
        + (f" ({', '.join(env_vars.keys())})" if env_vars else " (none)"),
        "",
        "── AWS current state ─────────────────────────────────────────",
        *aws_status,
        "",
        "── QUESTIONS TO ASK THE USER BEFORE DEPLOYING ───────────────",
        "Ask the user each of the following. Use their answers to call update_deploy_config().",
        "",
        "1. container_name  — What should the container be called?",
        f"   Current value  : {container or '(not set)'}",
        "",
        "2. container_port  — Which port does your app listen on inside the container?",
        f"   Current value  : {port or '(not set)'}",
        "",
        "3. image.tag       — Which Docker image tag to deploy?",
        f"   Current value  : {tag}",
        "",
        "4. desired_count   — How many task replicas should run?",
        f"   Current value  : {count}",
        "",
        "5. cpu / memory    — Task CPU units and memory (MB)?",
        f"   Current value  : cpu={cpu}, memory={memory}",
        "",
    ]

    if service_exists:
        out += [
            "⚠  SERVICE ALREADY EXISTS — confirm the user understands this will do a rolling update.",
            "   If they want to add/change an ALB, the service must be deleted first (manual step in AWS Console).",
            "",
        ]

    out += [
        "Once you have all answers, call update_deploy_config() with the new values,",
        "then call deploy() to start the deployment.",
    ]

    return "\n".join(out)


@mcp.tool()
def update_deploy_config(
    deploy_yaml: str,
    container_name: str = "",
    container_port: int = 0,
    image_tag: str = "",
    desired_count: int = 0,
    cpu: int = 0,
    memory: int = 0,
) -> str:
    """
    Update specific fields in deploy.yaml with values provided by the user.
    Only fields with non-empty / non-zero values are written — others are left unchanged.

    Args:
        deploy_yaml:    Path to the deploy.yaml config file.
        container_name: New container name (leave empty to keep current).
        container_port: New container port (leave 0 to keep current).
        image_tag:      New Docker image tag (leave empty to keep current).
        desired_count:  New desired task count (leave 0 to keep current).
        cpu:            New CPU units (leave 0 to keep current).
        memory:         New memory MB (leave 0 to keep current).
    """
    import yaml as _yaml

    p = Path(deploy_yaml).resolve()
    if not p.exists():
        return f"ERROR: deploy.yaml not found: {deploy_yaml}"

    cfg = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    ecs_block   = cfg.setdefault("ecs", {})
    image_block = cfg.setdefault("image", {})

    changed: list[str] = []

    if container_name:
        old = ecs_block.get("container_name", "")
        ecs_block["container_name"] = container_name
        changed.append(f"  container_name : {old!r} → {container_name!r}")

    if container_port:
        old = ecs_block.get("container_port", "")
        ecs_block["container_port"] = container_port
        changed.append(f"  container_port : {old!r} → {container_port!r}")

    if image_tag:
        old = image_block.get("tag", "")
        image_block["tag"] = image_tag
        changed.append(f"  image.tag      : {old!r} → {image_tag!r}")

    if desired_count:
        old = ecs_block.get("desired_count", "")
        ecs_block["desired_count"] = desired_count
        changed.append(f"  desired_count  : {old!r} → {desired_count!r}")

    if cpu:
        old = ecs_block.get("cpu", "")
        ecs_block["cpu"] = cpu
        changed.append(f"  cpu            : {old!r} → {cpu!r}")

    if memory:
        old = ecs_block.get("memory", "")
        ecs_block["memory"] = memory
        changed.append(f"  memory         : {old!r} → {memory!r}")

    if not changed:
        return "No changes made — all provided values were empty/zero."

    p.write_text(_yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")

    return "deploy.yaml updated:\n" + "\n".join(changed)


def _load_deploy_yaml(deploy_yaml: str) -> dict | str:
    """Return parsed deploy.yaml dict, or an error string."""
    p = Path(deploy_yaml).resolve()
    if not p.exists():
        return f"ERROR: deploy.yaml not found: {deploy_yaml}"
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return f"ERROR parsing deploy.yaml: {exc}"


@mcp.tool()
def get_ecs_diagnostics(deploy_yaml: str, log_minutes: int = 10) -> str:
    """
    Pull a full diagnostic snapshot from AWS for a failed or unhealthy deployment:
      - ECS service status (running/desired/pending counts, rollout state)
      - Last 10 ECS service events (shows why tasks stopped)
      - Stopped task exit codes and stopped reasons (the root cause)
      - Last 60 lines from CloudWatch Logs (/ecs/<task_family>)
      - ECR image existence check

    Call this whenever a deployment fails or the health check keeps failing.
    Claude will read the output and diagnose the root cause automatically.

    Args:
        deploy_yaml: Path to the deploy.yaml config file.
        log_minutes: How many minutes of CloudWatch logs to fetch (default 10).
    """
    cfg = _load_deploy_yaml(deploy_yaml)
    if isinstance(cfg, str):
        return cfg

    region        = cfg.get("region", "us-east-1")
    cluster       = cfg.get("cluster", "")
    service_name  = (cfg.get("ecs") or {}).get("service_name", "")
    task_family   = (cfg.get("ecs") or {}).get("task_family", "")
    repo_uri      = (cfg.get("image") or {}).get("repository", "")
    image_tag     = (cfg.get("image") or {}).get("tag", "latest")

    sections: list[str] = [
        f"ECS Diagnostics for service '{service_name}' in cluster '{cluster}' ({region})",
        f"Requested at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
    ]

    ecs    = boto3.client("ecs",    region_name=region)
    logs   = boto3.client("logs",   region_name=region)
    ecr    = boto3.client("ecr",    region_name=region)

    # ── 1. ECS Service status ─────────────────────────────────────────────────
    sections.append("═" * 60)
    sections.append("1. ECS SERVICE STATUS")
    sections.append("─" * 60)
    try:
        resp = ecs.describe_services(cluster=cluster, services=[service_name])
        svcs = resp.get("services", [])
        if not svcs or svcs[0]["status"] == "INACTIVE":
            sections.append(f"  Service '{service_name}' does not exist or is INACTIVE.")
        else:
            svc = svcs[0]
            sections.append(f"  Status        : {svc.get('status')}")
            sections.append(f"  Running tasks : {svc.get('runningCount', 0)}")
            sections.append(f"  Desired tasks : {svc.get('desiredCount', 0)}")
            sections.append(f"  Pending tasks : {svc.get('pendingCount', 0)}")
            deps = svc.get("deployments", [])
            if deps:
                d = deps[0]
                sections.append(f"  Rollout state : {d.get('rolloutState', 'UNKNOWN')}")
                sections.append(f"  Rollout info  : {d.get('rolloutStateReason', '')}")
            sections.append("")

            # ── 2. Service events ─────────────────────────────────────────────
            sections.append("═" * 60)
            sections.append("2. LAST 10 SERVICE EVENTS  (most recent first)")
            sections.append("─" * 60)
            events = svc.get("events", [])[:10]
            for ev in events:
                ts = ev.get("createdAt", "")
                if hasattr(ts, "strftime"):
                    ts = ts.strftime("%H:%M:%S")
                sections.append(f"  [{ts}] {ev.get('message', '')}")
    except ClientError as exc:
        sections.append(f"  AWS error: {exc}")

    sections.append("")

    # ── 3. Stopped task exit codes + reasons ─────────────────────────────────
    sections.append("═" * 60)
    sections.append("3. RECENTLY STOPPED TASKS  (exit codes + crash reasons)")
    sections.append("─" * 60)
    try:
        stopped = ecs.list_tasks(cluster=cluster, family=task_family, desiredStatus="STOPPED")
        task_arns = stopped.get("taskArns", [])[:5]
        if not task_arns:
            sections.append("  No recently stopped tasks found.")
        else:
            described = ecs.describe_tasks(cluster=cluster, tasks=task_arns)
            for task in described.get("tasks", []):
                task_id = task.get("taskArn", "").split("/")[-1][:16]
                stopped_at  = task.get("stoppedAt",  "")
                stopped_reason = task.get("stoppedReason", "")
                if hasattr(stopped_at, "strftime"):
                    stopped_at = stopped_at.strftime("%H:%M:%S")
                sections.append(f"  Task {task_id}... stopped at {stopped_at}")
                sections.append(f"    Reason: {stopped_reason}")
                for c in task.get("containers", []):
                    exit_code = c.get("exitCode")
                    reason    = c.get("reason", "")
                    name      = c.get("name", "")
                    sections.append(f"    Container '{name}': exit={exit_code}  {reason}")
                sections.append("")
    except ClientError as exc:
        sections.append(f"  AWS error: {exc}")

    sections.append("")

    # ── 4. CloudWatch logs ────────────────────────────────────────────────────
    log_group = f"/ecs/{task_family}"
    sections.append("═" * 60)
    sections.append(f"4. CLOUDWATCH LOGS  ({log_group}, last {log_minutes} min)")
    sections.append("─" * 60)
    try:
        start_ms = int((time.time() - log_minutes * 60) * 1000)
        streams_resp = logs.describe_log_streams(
            logGroupName=log_group,
            orderBy="LastEventTime",
            descending=True,
            limit=3,
        )
        streams = streams_resp.get("logStreams", [])
        if not streams:
            sections.append(f"  No log streams found in {log_group}.")
            sections.append("  → The task may never have started, or the log group doesn't exist yet.")
        else:
            for stream in streams:
                stream_name = stream["logStreamName"]
                sections.append(f"  Stream: {stream_name}")
                events_resp = logs.get_log_events(
                    logGroupName=log_group,
                    logStreamName=stream_name,
                    startTime=start_ms,
                    limit=60,
                    startFromHead=False,
                )
                log_events = events_resp.get("events", [])
                if not log_events:
                    sections.append("    (no events in this time window)")
                else:
                    for ev in log_events:
                        msg = ev.get("message", "").rstrip()
                        sections.append(f"    {msg}")
                sections.append("")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            sections.append(f"  Log group '{log_group}' does not exist.")
            sections.append("  → Task hasn't written any logs yet. Check stopped task reasons above.")
        else:
            sections.append(f"  AWS error: {exc}")

    sections.append("")

    # ── 5. ECR image check ────────────────────────────────────────────────────
    sections.append("═" * 60)
    sections.append("5. ECR IMAGE CHECK")
    sections.append("─" * 60)
    try:
        repo_name = repo_uri.split("/", 1)[-1] if "/" in repo_uri else repo_uri
        ecr.describe_images(repositoryName=repo_name, imageIds=[{"imageTag": image_tag}])
        sections.append(f"  ✓ Image {repo_uri}:{image_tag} exists in ECR.")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ImageNotFoundException", "RepositoryNotFoundException"):
            sections.append(f"  ✗ Image {repo_uri}:{image_tag} NOT FOUND in ECR.")
            sections.append("  → The push may have failed. Try redeploying.")
        else:
            sections.append(f"  AWS error: {exc}")

    sections.append("")
    sections.append("═" * 60)
    sections.append(
        "Diagnose the root cause from the data above and propose a concrete fix. "
        "Common causes:\n"
        "  • exit=1 / exit=137 in containers → app crash on startup (check logs)\n"
        "  • 'CannotPullContainerError' → ECR auth or image not found\n"
        "  • 'Essential container exited' + no logs → CMD/ENTRYPOINT wrong\n"
        "  • Health check failing → /health route missing or wrong port\n"
        "  • 'ResourceInitializationError' → missing executionRoleArn or bad secrets"
    )

    return "\n".join(sections)


if __name__ == "__main__":
    mcp.run()
