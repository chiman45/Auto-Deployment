"""
chims-deployment MCP server — Claude Code plugin for AWS ECS deployments.

Provides tools for Dockerfile/K8s validation, ECS deployment, live log streaming,
and automated diagnostics. Claude Code performs all AI analysis — no API key needed.

Tools:
  - prepare_deploy       : review config + check AWS state before deploying
  - update_deploy_config : write user-confirmed values back to deploy.yaml
  - deploy               : start deployment in background, stream logs to file
  - get_deploy_logs      : tail live deployment output
  - get_service_logs     : tail CloudWatch logs for the running service
  - get_ecs_diagnostics  : full diagnostic snapshot (events + logs + exit codes)
  - validate_dockerfile  : read Dockerfile for Claude to analyze
  - validate_k8s_manifest: read K8s YAML for Claude to analyze
  - pre_deploy_check     : scan all files in build context before deploy
  - apply_fix            : write corrected file content (user must approve first)
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

_active_deploys: dict[str, dict] = {}

_K8S_DIRS = ("k8s", "kubernetes", "manifests", "deploy", "infra", "helm")
_EXCLUDED_NAMES = {"deploy.yaml", "deploy.yml", "deployagent.yaml", "deployagent.yml"}

mcp = FastMCP("chims-deployment")


# ---------------------------------------------------------------------------
# Validation tools
# ---------------------------------------------------------------------------

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
        f"{'-' * 40}\n"
        f"{content}\n"
        f"{'-' * 40}\n"
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
        return f"Skipped {p.name} -- this is a deployagent config file, not a K8s manifest."

    content = p.read_text(encoding="utf-8")
    lines = content.splitlines()

    return (
        f"K8s/YAML manifest at: {p}\n"
        f"Lines: {len(lines)}\n"
        f"{'-' * 40}\n"
        f"{content}\n"
        f"{'-' * 40}\n"
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

    Args:
        build_context: Path to the project root / build context directory.
        dockerfile:    Relative path to the Dockerfile (default: Dockerfile).
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

        sections.append("=" * 50)
        sections.append(f"FILE: {p.name}  ({file_type})")
        sections.append(f"PATH: {p}")
        sections.append("-" * 50)
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
    Write corrected content to a file. Use ONLY after the user has explicitly
    reviewed and approved the fix.

    Args:
        path:              Absolute or relative path to the file to fix.
        corrected_content: The complete corrected file content to write.
    """
    p = Path(path).resolve()
    if not p.exists():
        return f"ERROR: File not found: {path}. Will not create new files via this tool."

    original = p.read_text(encoding="utf-8")
    if original == corrected_content:
        return "No changes needed -- content is identical to current file."

    p.write_text(corrected_content, encoding="utf-8")
    return (
        f"[OK] Written to {p}. "
        f"Original had {len(original.splitlines())} lines, "
        f"new content has {len(corrected_content.splitlines())} lines."
    )


# ---------------------------------------------------------------------------
# Deployment tools
# ---------------------------------------------------------------------------

def _stream_to_file(proc: subprocess.Popen, log_path: Path) -> None:
    with open(log_path, "w", encoding="utf-8") as f:
        for line in proc.stdout:  # type: ignore[union-attr]
            f.write(line)
            f.flush()
    proc.wait()


@mcp.tool()
def prepare_deploy(deploy_yaml: str) -> str:
    """
    ALWAYS call this before deploy(). Reads the current deploy.yaml, checks AWS
    for existing resources, and returns a list of questions to ask the user.

    After collecting answers, call update_deploy_config() then deploy().

    Args:
        deploy_yaml: Path to the deploy.yaml config file.
    """
    p = Path(deploy_yaml).resolve()
    cfg: dict = {}

    if p.exists():
        try:
            cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
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

    aws_status: list[str] = []
    service_exists = False

    try:
        ecs_client = boto3.client("ecs", region_name=region)

        if task_family:
            try:
                td_resp = ecs_client.describe_task_definition(taskDefinition=task_family)
                rev = str(td_resp["taskDefinition"].get("revision", "?"))
                aws_status.append(f"  Task definition : {task_family}:{rev}  [EXISTS]")
            except ClientError:
                aws_status.append(f"  Task definition : {task_family}  [NOT FOUND - will create]")

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
                        f"  ECS service     : {service_name}  [EXISTS - {running}/{desired} tasks running]"
                    )
                    lbs = svc.get("loadBalancers", [])
                    aws_status.append(
                        f"  Load balancer   : {'attached' if lbs else 'NOT attached'}"
                        + ("" if lbs else " (delete + recreate service to add one)")
                    )
                else:
                    aws_status.append(f"  ECS service     : {service_name}  [NOT FOUND - will create]")
            except ClientError:
                aws_status.append(f"  ECS service     : {service_name}  [NOT FOUND - will create]")

        if alb_name:
            try:
                alb_client = boto3.client("elbv2", region_name=region)
                alb_resp = alb_client.describe_load_balancers(Names=[alb_name])
                lbs = alb_resp.get("LoadBalancers", [])
                if lbs:
                    dns = lbs[0].get("DNSName", "?")
                    aws_status.append(f"  ALB             : {alb_name}  [EXISTS - {dns}]")
                else:
                    aws_status.append(f"  ALB             : {alb_name}  [NOT FOUND - will create]")
            except ClientError:
                aws_status.append(f"  ALB             : {alb_name}  [NOT FOUND - will create]")

    except Exception as exc:
        aws_status.append(f"  (AWS lookup error: {exc})")

    out: list[str] = [
        "PRE-DEPLOY CONFIGURATION REVIEW",
        f"Config file: {p}",
        "",
        "-- Current deploy.yaml values --",
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
        "-- AWS current state --",
        *aws_status,
        "",
        "-- QUESTIONS TO ASK THE USER BEFORE DEPLOYING --",
        "Ask the user each of the following. Use answers to call update_deploy_config().",
        "",
        "1. container_name  -- What should the container be called?",
        f"   Current: {container or '(not set)'}",
        "",
        "2. container_port  -- Which port does your app listen on inside the container?",
        f"   Current: {port or '(not set)'}",
        "",
        "3. image.tag       -- Which Docker image tag to deploy?",
        f"   Current: {tag}",
        "",
        "4. desired_count   -- How many task replicas?",
        f"   Current: {count}",
        "",
        "5. cpu / memory    -- Task CPU units and memory (MB)?",
        f"   Current: cpu={cpu}, memory={memory}",
        "",
    ]

    if service_exists:
        out += [
            "[WARNING] SERVICE ALREADY EXISTS -- this will do a rolling update.",
            "To add or change an ALB, delete the ECS service first (AWS Console).",
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
    Update specific fields in deploy.yaml with values confirmed by the user.
    Only non-empty / non-zero values are written; others are left unchanged.

    Args:
        deploy_yaml:    Path to the deploy.yaml config file.
        container_name: New container name (empty = keep current).
        container_port: New container port (0 = keep current).
        image_tag:      New Docker image tag (empty = keep current).
        desired_count:  New desired task count (0 = keep current).
        cpu:            New CPU units (0 = keep current).
        memory:         New memory MB (0 = keep current).
    """
    p = Path(deploy_yaml).resolve()
    if not p.exists():
        return f"ERROR: deploy.yaml not found: {deploy_yaml}"

    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    ecs_block   = cfg.setdefault("ecs", {})
    image_block = cfg.setdefault("image", {})
    changed: list[str] = []

    if container_name:
        old = ecs_block.get("container_name", "")
        ecs_block["container_name"] = container_name
        changed.append(f"  container_name : {old!r} -> {container_name!r}")

    if container_port:
        old = ecs_block.get("container_port", "")
        ecs_block["container_port"] = container_port
        changed.append(f"  container_port : {old!r} -> {container_port!r}")

    if image_tag:
        old = image_block.get("tag", "")
        image_block["tag"] = image_tag
        changed.append(f"  image.tag      : {old!r} -> {image_tag!r}")

    if desired_count:
        old = ecs_block.get("desired_count", "")
        ecs_block["desired_count"] = desired_count
        changed.append(f"  desired_count  : {old!r} -> {desired_count!r}")

    if cpu:
        old = ecs_block.get("cpu", "")
        ecs_block["cpu"] = cpu
        changed.append(f"  cpu            : {old!r} -> {cpu!r}")

    if memory:
        old = ecs_block.get("memory", "")
        ecs_block["memory"] = memory
        changed.append(f"  memory         : {old!r} -> {memory!r}")

    if not changed:
        return "No changes made -- all provided values were empty/zero."

    p.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    return "deploy.yaml updated:\n" + "\n".join(changed)


@mcp.tool()
def deploy(deploy_yaml: str, skip_validate: bool = False) -> str:
    """
    Start a deployment in the background and return immediately.
    Output is streamed to a log file in real time.
    Call get_deploy_logs() to check progress.

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

    log_path = Path(tempfile.gettempdir()) / f"chims_deploy_{p.stem}_{int(time.time())}.log"

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
    threading.Thread(target=_stream_to_file, args=(proc, log_path), daemon=True).start()

    return (
        f"Deployment started (PID {proc.pid}).\n"
        f"Log file: {log_path}\n\n"
        f"Call get_deploy_logs(\"{deploy_yaml}\") every 10-15 seconds to see live progress."
    )


@mcp.tool()
def get_deploy_logs(deploy_yaml: str, last_n_lines: int = 30) -> str:
    """
    Read the live deployment log to check progress.
    Call repeatedly while a deployment is running.

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
        f"{'-' * 50}\n"
        f"{tail}"
    )


@mcp.tool()
def get_service_logs(deploy_yaml: str, lines: int = 100, minutes: int = 30, stream: str = "") -> str:
    """
    Tail CloudWatch logs for the deployed ECS service.
    Reads deploy.yaml to find the log group automatically.

    Args:
        deploy_yaml: Path to the deploy.yaml config file.
        lines:       Max log lines per stream (default 100).
        minutes:     How far back to look in minutes (default 30).
        stream:      Specific log stream name (default: latest 3 streams).
    """
    p = Path(deploy_yaml).resolve()
    if not p.exists():
        return f"ERROR: deploy.yaml not found: {deploy_yaml}"

    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return f"ERROR parsing deploy.yaml: {exc}"

    region      = cfg.get("region", "us-east-1")
    task_family = (cfg.get("ecs") or {}).get("task_family", "")
    service     = (cfg.get("ecs") or {}).get("service_name", "")
    log_group   = f"/ecs/{task_family}"
    start_ms    = int((time.time() - minutes * 60) * 1000)

    cw = boto3.client("logs", region_name=region)

    out: list[str] = [
        "CloudWatch Logs",
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
            out.append("The service may not have started yet, or the log group does not exist.")
            return "\n".join(out)

        for s in streams:
            sname = s["logStreamName"] if isinstance(s, dict) else s
            out.append("-" * 60)
            out.append(f"Stream: {sname}")
            out.append("-" * 60)
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
def get_ecs_diagnostics(deploy_yaml: str, log_minutes: int = 10) -> str:
    """
    Pull a full diagnostic snapshot for a failed or unhealthy deployment:
      - ECS service status (running/desired/pending counts, rollout state)
      - Last 10 ECS service events
      - Stopped task exit codes and crash reasons
      - Last 60 lines from CloudWatch Logs
      - ECR image existence check

    Call this whenever a deployment fails or the health check keeps failing.

    Args:
        deploy_yaml: Path to the deploy.yaml config file.
        log_minutes: How many minutes of CloudWatch logs to fetch (default 10).
    """
    p = Path(deploy_yaml).resolve()
    if not p.exists():
        return f"ERROR: deploy.yaml not found: {deploy_yaml}"

    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return f"ERROR parsing deploy.yaml: {exc}"

    region       = cfg.get("region", "us-east-1")
    cluster      = cfg.get("cluster", "")
    service_name = (cfg.get("ecs") or {}).get("service_name", "")
    task_family  = (cfg.get("ecs") or {}).get("task_family", "")
    repo_uri     = (cfg.get("image") or {}).get("repository", "")
    image_tag    = (cfg.get("image") or {}).get("tag", "latest")

    sections: list[str] = [
        f"ECS Diagnostics -- service '{service_name}' in cluster '{cluster}' ({region})",
        f"Requested at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
    ]

    ecs  = boto3.client("ecs",  region_name=region)
    logs = boto3.client("logs", region_name=region)
    ecr  = boto3.client("ecr",  region_name=region)

    # 1. ECS service status
    sections += ["=" * 60, "1. ECS SERVICE STATUS", "-" * 60]
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
                sections.append(f"  Rollout state : {deps[0].get('rolloutState', 'UNKNOWN')}")
                sections.append(f"  Rollout info  : {deps[0].get('rolloutStateReason', '')}")
            sections.append("")

            # 2. Service events
            sections += ["=" * 60, "2. LAST 10 SERVICE EVENTS", "-" * 60]
            for ev in svc.get("events", [])[:10]:
                ts = ev.get("createdAt", "")
                if hasattr(ts, "strftime"):
                    ts = ts.strftime("%H:%M:%S")
                sections.append(f"  [{ts}] {ev.get('message', '')}")
    except ClientError as exc:
        sections.append(f"  AWS error: {exc}")

    sections.append("")

    # 3. Stopped tasks
    sections += ["=" * 60, "3. RECENTLY STOPPED TASKS", "-" * 60]
    try:
        stopped = ecs.list_tasks(cluster=cluster, family=task_family, desiredStatus="STOPPED")
        task_arns = stopped.get("taskArns", [])[:5]
        if not task_arns:
            sections.append("  No recently stopped tasks found.")
        else:
            described = ecs.describe_tasks(cluster=cluster, tasks=task_arns)
            for task in described.get("tasks", []):
                task_id = task.get("taskArn", "").split("/")[-1][:16]
                stopped_at = task.get("stoppedAt", "")
                if hasattr(stopped_at, "strftime"):
                    stopped_at = stopped_at.strftime("%H:%M:%S")
                sections.append(f"  Task {task_id}... stopped at {stopped_at}")
                sections.append(f"    Reason: {task.get('stoppedReason', '')}")
                for c in task.get("containers", []):
                    sections.append(
                        f"    Container '{c.get('name', '')}': "
                        f"exit={c.get('exitCode')}  {c.get('reason', '')}"
                    )
                sections.append("")
    except ClientError as exc:
        sections.append(f"  AWS error: {exc}")

    sections.append("")

    # 4. CloudWatch logs
    log_group = f"/ecs/{task_family}"
    sections += ["=" * 60, f"4. CLOUDWATCH LOGS  ({log_group}, last {log_minutes} min)", "-" * 60]
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
            sections.append("  The task may never have started, or the log group does not exist.")
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
                for ev in events_resp.get("events", []):
                    sections.append(f"    {ev.get('message', '').rstrip()}")
                sections.append("")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            sections.append(f"  Log group '{log_group}' does not exist.")
        else:
            sections.append(f"  AWS error: {exc}")

    sections.append("")

    # 5. ECR image check
    sections += ["=" * 60, "5. ECR IMAGE CHECK", "-" * 60]
    try:
        repo_name = repo_uri.split("/", 1)[-1] if "/" in repo_uri else repo_uri
        ecr.describe_images(repositoryName=repo_name, imageIds=[{"imageTag": image_tag}])
        sections.append(f"  [OK] Image {repo_uri}:{image_tag} exists in ECR.")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ImageNotFoundException", "RepositoryNotFoundException"):
            sections.append(f"  [FAIL] Image {repo_uri}:{image_tag} NOT FOUND in ECR.")
            sections.append("  The push may have failed. Try redeploying.")
        else:
            sections.append(f"  AWS error: {exc}")

    sections += [
        "",
        "=" * 60,
        "Diagnose the root cause and propose a fix. Common causes:",
        "  exit=1 / exit=137    -> app crash on startup (check logs above)",
        "  CannotPullContainerError -> ECR auth or image not found",
        "  Essential container exited + no logs -> CMD/ENTRYPOINT wrong",
        "  Health check failing  -> /health route missing or wrong port",
        "  ResourceInitializationError -> missing executionRoleArn or bad secrets",
    ]

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
