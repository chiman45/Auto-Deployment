Custom-CICD YAML Auto-Fixer

A Python + Docker–based AI agent that automatically detects and fixes YAML file errors in a GitHub repository using Google Gemini.
It creates a new branch, commits the fixes, and opens a Pull Request — fully automated ✨

🚀 Features

✅ Clones a GitHub repository securely using a personal access token (PAT) from your .env.
✅ Recursively scans all .yaml and .yml files.
✅ Detects and logs YAML syntax or formatting errors.
✅ Uses Gemini 2.5 Flash API to:

Automatically repair invalid YAML.

Explain the root cause and applied fix.
✅ Commits and pushes all corrections to a new branch.
✅ Automatically creates a Pull Request (PR) for review and merge.
✅ Logs all actions in agent_log.txt.

⚙️ Requirements

Python 3.10+

Docker (recommended)

Google Gemini API Key

GitHub Personal Access Token (PAT) with:

repo (for private repos)

public_repo (for public repos)

📦 Dependencies

If running locally (without Docker):

pip install gitpython ruamel.yaml google-generativeai python-dotenv requests


Or, if you’re using Docker:

docker build -t myagent:latest .

🔐 Environment Variables

Create a .env file in your project root:

GEMINI_API_KEY=your_gemini_api_key
GITHUB_USERNAME=your_github_username
GITHUB_TOKEN=your_github_pat


✅ Keep .env in your .gitignore — it contains sensitive info.

🐳 Run via Docker
docker run -it --env-file .env --name agent_container myagent:latest


The container will prompt you for:

GitHub repository URL

Local directory name

Commit message

Then it will:

Clone your repo

Fix YAML errors

Push fixes on a new branch

Open a pull request automatically

🧩 Example Workflow
Enter the GitHub repository URL: https://github.com/username/project
Enter the local directory name: repo-local
Enter the commit message: fix yaml configs

→ Cloning repository...
→ Found and fixed 3 YAML errors.
→ Committed and pushed branch yaml-fix-20251022.
→ Pull Request created: https://github.com/username/project/pull/12

🧾 Logging

All operations are recorded in agent_log.txt:

File names with detected errors

Gemini’s explanations of fixes

Corrected YAML content

Branch name, commit details, and PR link

🧰 Development Notes

Uses gemini-2.5-flash-001 model (automatically fallback to Pro if unavailable).

Automatically creates unique branch names (e.g. yaml-fix-20251022152206).

Safe to run multiple times — each execution generates a new branch and PR.

🧑‍💻 Local Development Mode

For live-coding (no rebuild needed after edits):

docker run -it --rm --env-file .env -v .:/app --name agent_dev python:3.10-slim bash
pip install -r requirements.txt
python main.py