# Custom-CICD YAML Auto-Fixer

A Python-based agent that automatically detects errors in YAML files within a GitHub repository, fixes them using Google Gemini API, and logs all actions. 
Designed for CI/CD pipelines and project repositories to maintain valid YAML configurations.

# Features

Clones a GitHub repository locally.

Recursively scans all YAML files (*.yaml / *.yml).

Detects syntax or formatting errors in YAML files.

Uses Gemini API to:

Automatically fix broken YAML.

Explain the errors and how they were fixed.

Logs errors, fixes, and explanations in agent_log.txt.

Commits and pushes fixes to GitHub (current branch or a new branch).

# Requirements

Python 3.10+

Packages:
```bash
pip install gitpython ruamel.yaml google-generativeai python-dotenv
```

Gemini API key from Google Cloud.

Create a .env file in the project root:
```bash
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
```

Add .env to .gitignore (already included).

Install dependencies:
```bash
pip install -r requirements.txt
```


Usage

Configure the target repo in main.py:

repo_url = "https://github.com/<username>/<repo>.git"


Run the agent:
```bash
python main.py
```

# The script will:

Clone the repo if not already cloned.

Scan for YAML errors.

Fix broken YAML using Gemini API.

Log actions in agent_log.txt.

Commit and push fixes to GitHub.


# Notes

By default, the script works on the current branch.

For safety, it is recommended to create a new branch for automatic fixes.

Avoid committing your .env file; it contains sensitive API keys.

# Logging

All actions are logged in agent_log.txt:

File errors detected.

Gemini’s explanation of the issue.

Fixed YAML content.

Commit and branch information.

# License

MIT License – feel free to use and modify this project
