import git
import glob
import ruamel.yaml
import google.generativeai as genai
import os
import subprocess
from datetime import datetime
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Gemini API key configuration
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Taking input from user for repo URL and local directory
repo_url = input("Enter the GitHub repository URL: ").strip()
local_dir = input("Enter the local directory name: ").strip()
commit_message = input("Enter the commit message: ").strip()


# Repo config
repo_url = repo_url
local_dir = local_dir

# Log file
log_file = "agent_log.txt"

def log_message(msg):
    with open(log_file, "a", encoding="utf-8") as log:
        log.write(f"{datetime.now()} - {msg}\n")
    print(msg)


# Clone repo if not exists
if not os.path.exists(local_dir):
    git.Repo.clone_from(repo_url, local_dir)
repo = git.Repo(local_dir)

# Parsing YAML files
yaml_files = glob.glob(f"{local_dir}/**/*.y*ml", recursive=True)
yaml_parser = ruamel.yaml.YAML(typ="safe")
yaml_parser.preserve_quotes = True
yaml_parser.allow_duplicate_keys = False
yaml_parser.indent(mapping=2, sequence=4, offset=2)

for file in yaml_files:
    try:
        with open(file, "r") as f:
            content = f.read()

        # Run yamllint for strict indentation & style
        result = subprocess.run(
            ["yamllint", "-f", "parsable", file],
            capture_output=True, text=True
        )

        if result.returncode != 0:
            log_message(f"Formatting/indentation issues in {file}:")
            log_message(result.stdout.strip())

        # Try parsing with ruamel.yaml (strict syntax check)
        yaml_parser.load(content)
        log_message(f"{file} is valid YAML (syntax check passed)")

    except Exception as e:
        error_msg = str(e)
        log_message(f"Error in {file}: {error_msg}")

        # Ask Gemini to fix and explain
        model = genai.GenerativeModel("gemini-2.0-flash")
        prompt = f"""
        You are a YAML repair assistant.
        - Input: A YAML file with error.
        - Task: Fix the YAML so it's valid and logically correct.
        - Also explain the issue and how you fixed it.
        Error: {error_msg}
        File: {file}
        Content:
        {content}
        """

        response = model.generate_content(prompt)
        fixed_output = response.text.strip()

        # Separate explanation from YAML if model mixes
        if "```yaml" in fixed_output:
            explanation, yaml_fixed = fixed_output.split("```yaml", 1)
            yaml_fixed = yaml_fixed.split("```", 1)[0].strip()
        else:
            explanation = "Model did not provide explicit explanation separately."
            yaml_fixed = fixed_output

        # Save fixed YAML
        with open(file, "w", encoding="utf-8") as fw:
            fw.write(yaml_fixed)

        # Print & log
        log_message(f"Fix applied to {file}")
        log_message(f"Error: {error_msg}")
        log_message(f"Explanation: {explanation.strip()}")
        log_message(f"Fixed YAML:\n{yaml_fixed}\n")

# Commit and push
repo.git.add(A=True)
repo.index.commit(commit_message)
origin = repo.remote(name="origin")
origin.push()

log_message("All fixes committed and pushed successfully.")
