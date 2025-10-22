import git
import glob
import ruamel.yaml
import google.generativeai as genai
import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

# Config from .env
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Ask user for repo info
repo_url = input("Enter the GitHub repository URL: ").strip()
local_dir = input("Enter the local directory name: ").strip()
commit_message = input("Enter the commit message: ").strip()

# Log file setup
log_file = "agent_log.txt"

def log_message(msg):
    with open(log_file, "a", encoding="utf-8") as log:
        log.write(f"{datetime.now()} - {msg}\n")
    print(msg)

# Authenticated repo URL for Git operations
parsed = urlparse(repo_url)
authed_url = f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@{parsed.netloc}{parsed.path}"

# Clone or open existing repo
if not os.path.exists(local_dir):
    log_message(f"Cloning repository {repo_url}...")
    repo = git.Repo.clone_from(authed_url, local_dir)
else:
    log_message(f"Using existing local repo at {local_dir}")
    repo = git.Repo(local_dir)

# Create a new branch for fixes
branch_name = f"yaml-fix-{datetime.now().strftime('%Y%m%d%H%M%S')}"
repo.git.checkout('-b', branch_name)
log_message(f"Created new branch: {branch_name}")

# YAML validation and fixing
yaml_files = glob.glob(f"{local_dir}/**/*.y*ml", recursive=True)
yaml_parser = ruamel.yaml.YAML()

for file in yaml_files:
    try:
        with open(file, "r", encoding="utf-8") as f:
            content = f.read()
            yaml_parser.load(content)
            log_message(f"{file} is valid YAML")
    except Exception as e:
        error_msg = str(e)
        log_message(f"Error in {file}: {error_msg}")

        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""
        You are a YAML repair assistant.
        Fix this broken YAML and explain the issue.
        Error: {error_msg}
        File: {file}
        Content:
        {content}
        """

        response = model.generate_content(prompt)
        fixed_output = response.text.strip()

        # Extract YAML and explanation
        if "```yaml" in fixed_output:
            parts = fixed_output.split("```yaml", 1)
            explanation = parts[0].strip()
            yaml_fixed = parts[1].split("```", 1)[0].strip()
        else:
            explanation = "Model did not provide clear separation."
            yaml_fixed = fixed_output

        with open(file, "w", encoding="utf-8") as fw:
            fw.write(yaml_fixed)

        log_message(f"Fix applied to {file}")
        log_message(f"Explanation: {explanation}")
        log_message(f"Fixed YAML:\n{yaml_fixed}\n")

# Commit and push
repo.git.add(A=True)
repo.index.commit(commit_message)
origin = repo.remote(name="origin")
origin.push(refspec=f"{branch_name}:{branch_name}")
log_message(f"Pushed branch {branch_name} to remote.")

# Create a Pull Request
api_url = f"https://api.github.com/repos/{parsed.path.strip('/')}/pulls"
pr_data = {
    "title": f"Automated YAML Fixes - {datetime.now().strftime('%Y-%m-%d')}",
    "head": branch_name,
    "base": "main",
    "body": "This PR contains automated YAML error corrections made by the CI/CD Agent 🤖."
}
response = requests.post(api_url, json=pr_data, auth=(GITHUB_USERNAME, GITHUB_TOKEN))

if response.status_code == 201:
    pr_url = response.json()["html_url"]
    log_message(f"✅ Pull Request created successfully: {pr_url}")
else:
    log_message(f"❌ Failed to create Pull Request. Status: {response.status_code}")
    log_message(response.text)

log_message("All tasks completed successfully ✅")



#.\yaml_agent_venv\Scripts\python.exe main.py 