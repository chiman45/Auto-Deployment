import git
import glob
import ruamel.yaml
import google.generativeai as genai
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()  # Load .env file

# Gemini API key configuration
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Taking input from user for repo URL and local directory
repo_url = input("Enter the GitHub repository URL: ")
local_dir = input("Enter the local directory name: ")
commit_message = input("Enter the commit message: ")

# Repo config
repo_url = repo_url
local_dir = local_dir

#  Log file
log_file = "agent_log.txt"

def log_message(msg):
    with open(log_file, "a", encoding="utf-8") as log:  # <-- add encoding
        log.write(f"{datetime.now()} - {msg}\n")
    print(msg)  # still print to console




# Clone repo if not exists
if not os.path.exists(local_dir):
    git.Repo.clone_from(repo_url, local_dir)
repo = git.Repo(local_dir)

# Parsing YAML files
yaml_files = glob.glob(f"{local_dir}/**/*.y*ml", recursive=True)
yaml_parser = ruamel.yaml.YAML()

for file in yaml_files:
    try:
        with open(file, "r") as f:
            content = f.read()
            yaml_parser.load(content)  # check if valid
            log_message(f" {file} is valid YAML")

    except Exception as e:
        error_msg = str(e)
        log_message(f"Error in {file}: {error_msg}")

        # Ask Gemini to fix and explain
        model = genai.GenerativeModel("gemini-1.5-flash")  # flash because(paise nahi hai mere pass pro ke liye) for reasoning
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
        with open(file, "w") as fw:
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
