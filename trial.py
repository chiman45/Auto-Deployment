import os
import re
import glob
import git
import time
import shutil
import subprocess
import requests
import hashlib
import socket
import json
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
from dotenv import load_dotenv

import ruamel.yaml
import google.generativeai as genai


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not GEMINI_API_KEY:
    raise RuntimeError("❌ GEMINI_API_KEY missing in .env")
if not GITHUB_USERNAME or not GITHUB_TOKEN:
    raise RuntimeError("❌ GITHUB_USERNAME or GITHUB_TOKEN missing in .env")

genai.configure(api_key=GEMINI_API_KEY)

LOG_FILE = ROOT / "agent_log.txt"


def log_message(msg: str):
    line = f"{datetime.now()} - {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_cmd(cmd, cwd=None):
    shell = isinstance(cmd, str)
    printable = cmd if shell else " ".join(cmd)
    log_message(f"$ {printable}")

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=shell,
    )

    out_lines = []
    for line in proc.stdout:
        line = line.rstrip("\n")
        out_lines.append(line)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line)

    code = proc.wait()
    return code, "\n".join(out_lines)


def docker_available():
    return shutil.which("docker") is not None


MODEL_NAME = "gemini-2.5-flash"

def gemini_call(prompt: str, retries=3, backoff=8):
    last_err = None
    model = genai.GenerativeModel(MODEL_NAME)

    for i in range(retries):
        try:
            return model.generate_content(prompt)
        except Exception as e:
            last_err = e
            log_message(f"Gemini error: {e}. Retrying…")
            time.sleep(backoff * (i + 1))

    return model.generate_content(prompt)
def file_hash(path: Path):
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def context_fingerprint(root: Path):
    parts = []
    for rel in ["Dockerfile", "dockerfile", "requirements.txt", "package.json"]:
        p = root / rel
        if p.exists():
            parts.append(rel + ":" + file_hash(p))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def image_exists(tag: str):
    code, out = run_cmd(["docker", "images", "-q", tag])
    return code == 0 and out.strip() != ""


def container_exists(name: str):
    code, out = run_cmd(["docker", "ps", "-a", "--filter", f"name=^{name}$", "-q"])
    return code == 0 and out.strip() != ""


def free_port(port: int):
    p = port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except:
                p += 1


def parse_expose(df: Path):
    ports = []
    for line in df.read_text().splitlines():
        line = line.strip()
        if line.upper().startswith("EXPOSE"):
            for part in line.split()[1:]:
                p = part.split("/")[0]
                if p.isdigit():
                    ports.append(int(p))
    return ports


def default_tag(repo_root: Path, repo: git.Repo):
    try:
        sha = repo.git.rev_parse("--short", "HEAD").strip()
        return f"{repo_root.name}:{sha}"
    except:
        return f"{repo_root.name}:{int(time.time())}"


def ensure_remove(name: str):
    if container_exists(name):
        run_cmd(["docker", "rm", "-f", name])


def find_dockerfile(root: Path):
    for x in ["Dockerfile", "dockerfile"]:
        p = root / x
        if p.exists():
            return p
    for p in root.rglob("*Dockerfile"):
        return p
    return None


def find_compose(root: Path):
    for f in ["docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"]:
        p = root / f
        if p.exists():
            return p
    return None


# ------------------------------
# User Inputs
# ------------------------------
repo_url = input("GitHub repository URL: ").strip()
local_dir = input("Local directory name: ").strip()
commit_msg = input("Commit message: ").strip()
project_cmd = input("Project command to run (optional): ").strip()

AUTO_MODE = True
FORCE_REBUILD = False


# ------------------------------
# Clone repo
# ------------------------------
parsed = urlparse(repo_url)
authed_url = f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@{parsed.netloc}{parsed.path}"

repo_path = ROOT / local_dir
if not repo_path.exists():
    try:
        repo = git.Repo.clone_from(repo_url, repo_path)
    except:
        repo = git.Repo.clone_from(authed_url, repo_path)
else:
    repo = git.Repo(repo_path)

origin = repo.remote("origin")
origin.set_url(authed_url)

branch_name = f"auto-fix-{datetime.now().strftime('%Y%m%d%H%M%S')}"
repo.git.checkout("-b", branch_name)
log_message(f"Created branch {branch_name}")


# ------------------------------
# YAML Fixing
# ------------------------------
yaml_parser = ruamel.yaml.YAML()
yaml_files = glob.glob(str(repo_path / "**/*.yml"), recursive=True)
yaml_files += glob.glob(str(repo_path / "**/*.yaml"), recursive=True)

yaml_fixed = 0
yaml_failed = []

for y in yaml_files:
    try:
        raw = Path(y).read_text()
        yaml_parser.load(raw)
        log_message(f"{y} OK")
    except Exception as e:
        prompt = f"""
Fix YAML. Return only YAML.

Error:
{e}

Content:
{raw}
"""
        try:
            resp = gemini_call(prompt)
            text = resp.text.strip()
            fixed = text.split("```yaml",1)[1].split("```",1)[0].strip()
            yaml_parser.load(fixed)
            Path(y).write_text(fixed + "\n")
            yaml_fixed += 1
        except Exception as ee:
            yaml_failed.append((y,str(ee)))


# ------------------------------
# Dockerfile Build + Repair
# ------------------------------
def try_build(tag, df):
    ctx = str(df.parent)
    return run_cmd(["docker","build","-t",tag,"-f",str(df),ctx])


def repair_dockerfile(df, logs):
    original = df.read_text()
    prompt = f"""
Fix this Dockerfile. Return only Dockerfile.

Logs:
{logs}

Original:
{original}
"""
    resp = gemini_call(prompt)
    out = resp.text.strip()
    fixed = out.split("```dockerfile",1)[1].split("```",1)[0].strip()
    return fixed


docker_fixed = 0

if docker_available():
    df = find_dockerfile(repo_path)
    comp = find_compose(repo_path)

    if comp:
        log_message("Using docker compose")
        run_cmd("docker compose up -d", cwd=str(repo_path))

    elif df:
        tag = default_tag(repo_path, repo)
        fp = context_fingerprint(repo_path)
        fp_file = repo_path / ".fp.json"

        old_fp = None
        if fp_file.exists():
            try: old_fp = json.loads(fp_file.read_text())["fp"]
            except: pass

        need_build = FORCE_REBUILD or not image_exists(tag) or fp != old_fp

        if need_build:
            code, out = try_build(tag, df)
            if code != 0:
                for attempt in range(2):
                    try:
                        fixed = repair_dockerfile(df, out)
                        df.write_text(fixed + "\n")
                        docker_fixed += 1
                        code, out = try_build(tag, df)
                        if code == 0: break
                    except Exception as e:
                        log_message(f"Docker repair failed: {e}")
        fp_file.write_text(json.dumps({"fp":fp}))

        if image_exists(tag):
            name = f"{repo_path.name}-ctr"
            ensure_remove(name)

            args = ["docker","run","-d","--name",name]

            for p in parse_expose(df):
                host = free_port(p)
                args += ["-p",f"{host}:{p}"]

            env_file = None
            for x in [repo_path/".env", ROOT/".env"]:
                if x.exists(): env_file = str(x)

            if env_file:
                args += ["--env-file",env_file]

            args.append(tag)
            run_cmd(args)
            log_message(f"Started container {name}")
# ------------------------------
# Project Command Diagnostics & Auto-Repair
# ------------------------------
def extract_paths(text, root: Path):
    pattern = r"([A-Za-z0-9_./-]+\.(py|js|ts|json|yml|yaml|sh|md)|Dockerfile)"
    paths = set()
    for m in re.finditer(pattern, text):
        p = root / m.group(1)
        try:
            if p.exists():
                paths.add(p)
        except:
            pass
    return list(paths)


def repair_file(path: Path, logs: str):
    original = path.read_text()
    ext = path.suffix.lstrip(".") or "txt"

    prompt = f"""
Fix this file. Return only in ```{ext}``` fences.

Errors:
{logs}

Original:
{original}
"""
    try:
        resp = gemini_call(prompt)
        out = resp.text.strip()
        fixed = out.split(f"```{ext}",1)[1].split("```",1)[0].strip()
        if ext in ["yml","yaml"]:
            ruamel.yaml.YAML().load(fixed)
        path.write_text(fixed + "\n")
        return True
    except Exception as e:
        log_message(f"File repair failed: {e}")
        return False


project_fixes = 0

if project_cmd:
    code, output = run_cmd(project_cmd, cwd=str(repo_path))
    paths = extract_paths(output, repo_path)

    if paths:
        log_message(f"Detected {len(paths)} error files from logs")
        for p in paths:
            if repair_file(p, output):
                project_fixes += 1

        # optional re-run after repairs
        run_cmd(project_cmd, cwd=str(repo_path))


# ------------------------------
# Commit + Push + PR
# ------------------------------
suffix = []
if yaml_fixed: suffix.append(f"{yaml_fixed} YAML fixes")
if docker_fixed: suffix.append(f"{docker_fixed} Dockerfile fixes")
if project_fixes: suffix.append(f"{project_fixes} project fixes")

commit_suffix = " | ".join(suffix)
if commit_suffix:
    msg = f"{commit_msg} | {commit_suffix}"
else:
    msg = commit_msg

repo.git.add(A=True)
try:
    repo.index.commit(msg)
    log_message("Committed ✓")
except:
    log_message("Nothing to commit")

origin.push(refspec=f"{branch_name}:{branch_name}")
log_message("Pushed ✓")

# Detect default branch
meta = requests.get(
    f"https://api.github.com/repos/{parsed.path.strip('/')}",
    auth=(GITHUB_USERNAME, GITHUB_TOKEN)
)
base = "main"
if meta.ok:
    base = meta.json().get("default_branch","main")

payload = {
    "title": f"Automated Fixes - {datetime.now().strftime('%Y-%m-%d')}",
    "head": branch_name,
    "base": base,
    "body": "Automated YAML, Dockerfile & project command fixes.\nSee agent_log.txt."
}

resp = requests.post(
    f"https://api.github.com/repos/{parsed.path.strip('/')}/pulls",
    json=payload,
    auth=(GITHUB_USERNAME, GITHUB_TOKEN)
)

if resp.status_code == 201:
    log_message("PR created: " + resp.json()["html_url"])
else:
    log_message("PR failed: " + resp.text)

log_message("ALL DONE ✓")

#.\yaml_agent_venv\Scripts\python.exe main.py 