# Custom CI/CD Toolkit

A two-in-one DevOps toolkit:

1. **`deployagent`** — CLI tool to deploy any Dockerized app to AWS ECS with a single command
2. **GitHub Repo Analyzer** — AI-powered tool that clones, analyzes, and auto-fixes Kubernetes/Docker configs

---

## Table of Contents

- [deployagent — AWS Deployment CLI](#deployagent--aws-deployment-cli)
  - [How It Works](#how-it-works)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [AWS Setup](#aws-setup)
  - [Configuration](#configuration)
  - [Commands](#commands)
  - [Full Deployment Workflow](#full-deployment-workflow)
- [GitHub Repo Analyzer](#github-repo-analyzer)
  - [What It Does](#what-it-does)
  - [Analyzer Setup](#analyzer-setup)
  - [Running the Analyzer](#running-the-analyzer)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## deployagent — AWS Deployment CLI

Deploy any Dockerized project to AWS ECS Fargate with four commands: `plan`, `apply`, `rollback`, `status`.

### How It Works

```
Your project + deploy.yaml
        ↓
deployagent apply
        ↓
1. Builds Docker image
2. Pushes to ECR
3. Registers new ECS task definition
4. Updates ECS service
5. Applies CloudFormation changes (optional)
6. Waits for service stability
7. Runs health check
8. Saves deploy snapshot to SQLite (for rollback)
```

If the health check fails, it automatically rolls back to the last known-good state.

---

### Prerequisites

- Python 3.11+
- Docker Desktop (must be running during deploy)
- AWS account with IAM user credentials
- AWS CLI installed

---

### Installation

```bash
# Clone the repo
git clone https://github.com/chiman45/Custom-CICD.git
cd Custom-CICD

# Install deployagent
pip install -e .

# Verify installation
deployagent --help
```

---

### AWS Setup

#### 1. Configure credentials

Create a `.env` file in the project root (already gitignored):

```env
AWS_ACCESS_KEY_ID=your_access_key_here
AWS_SECRET_ACCESS_KEY=your_secret_key_here
AWS_DEFAULT_REGION=us-east-1
```

Or use the AWS CLI:

```bash
aws configure
```

Verify credentials work:

```bash
aws sts get-caller-identity
```

#### 2. Required IAM permissions

Your IAM user needs these permissions (attach in AWS Console → IAM → Users → your user → Add permissions):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecs:DescribeServices", "ecs:DescribeTaskDefinition",
        "ecs:RegisterTaskDefinition", "ecs:UpdateService",
        "ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability",
        "ecr:PutImage", "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
        "ecr:DescribeImages", "ecr:DescribeRepositories",
        "cloudformation:DescribeStacks", "cloudformation:CreateChangeSet",
        "cloudformation:DescribeChangeSet", "cloudformation:ExecuteChangeSet",
        "cloudformation:UpdateStack",
        "logs:DescribeLogStreams", "logs:GetLogEvents",
        "iam:PassRole"
      ],
      "Resource": "*"
    }
  ]
}
```

#### 3. Create AWS resources (one-time setup)

Run these once before your first deploy:

```bash
# Get your AWS account ID
aws sts get-caller-identity --query Account --output text

# Create ECR repository (replace YOUR_ACCOUNT_ID)
aws ecr create-repository --repository-name my-app --region us-east-1

# Create ECS cluster
aws ecs create-cluster --cluster-name my-cluster --region us-east-1

# Create IAM execution role for ECS tasks
aws iam create-role \
  --role-name ecsTaskExecutionRole \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Get your default VPC and subnet
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)
SUBNET=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC --query "Subnets[0].SubnetId" --output text)
SG=$(aws ec2 describe-security-groups --filters Name=vpc-id,Values=$VPC Name=group-name,Values=default --query "SecurityGroups[0].GroupId" --output text)

echo "VPC: $VPC | Subnet: $SUBNET | SG: $SG"

# Create CloudWatch log group
aws logs create-log-group --log-group-name /ecs/my-app-task --region us-east-1

# Register placeholder task definition (replace YOUR_ACCOUNT_ID)
aws ecs register-task-definition \
  --family my-app-task \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 256 --memory 512 \
  --execution-role-arn arn:aws:iam::YOUR_ACCOUNT_ID:role/ecsTaskExecutionRole \
  --container-definitions '[{"name":"my-app","image":"YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/my-app:latest","essential":true,"portMappings":[{"containerPort":8080,"protocol":"tcp"}],"logConfiguration":{"logDriver":"awslogs","options":{"awslogs-group":"/ecs/my-app-task","awslogs-region":"us-east-1","awslogs-stream-prefix":"ecs"}}}]'

# Create ECS service (replace SUBNET and SG with values from above)
aws ecs create-service \
  --cluster my-cluster \
  --service-name my-app-service \
  --task-definition my-app-task \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[SUBNET],securityGroups=[SG],assignPublicIp=ENABLED}"
```

---

### Configuration

Add a `deploy.yaml` to your project root:

```yaml
service: my-app                  # label used for deploy history
region: us-east-1                # AWS region
cluster: my-cluster              # ECS cluster name

image:
  repository: 123456789.dkr.ecr.us-east-1.amazonaws.com/my-app   # ECR repo URI
  tag: latest                    # image tag — use git SHA for unique deploys
  dockerfile: ./Dockerfile       # path to your Dockerfile
  build_context: .               # Docker build context

ecs:
  service_name: my-app-service   # ECS service name
  task_family: my-app-task       # task definition family
  container_name: my-app         # container name inside task definition
  container_port: 8080           # port your app listens on
  cpu: 256                       # 256 = 0.25 vCPU
  memory: 512                    # MB
  desired_count: 1               # number of running containers

# Optional — remove if not using CloudFormation
cloudformation:
  stack_name: my-app-infra
  template_file: ./infra/template.yaml
  parameters:
    Environment: production

health:
  endpoint: /health
  timeout: 30
  retries: 5
```

**Tip:** Use git commit SHA as the tag so every deploy is unique and traceable:

```bash
# Linux/macOS
TAG=$(git rev-parse --short HEAD)
sed -i "s/tag: .*/tag: $TAG/" deploy.yaml

# Windows PowerShell
$TAG = git rev-parse --short HEAD
(Get-Content deploy.yaml) -replace 'tag: .*', "tag: $TAG" | Set-Content deploy.yaml
```

---

### Commands

#### `plan` — Dry run, no AWS changes
```bash
deployagent plan deploy.yaml
```
Shows a colour-coded table of what will be created, updated, or left unchanged.

#### `apply` — Execute deployment
```bash
# With confirmation prompt
deployagent apply deploy.yaml

# Skip prompt (for CI/CD pipelines)
deployagent apply deploy.yaml --yes
```

#### `status` — Check service health
```bash
# Live health check
deployagent status deploy.yaml

# View full deploy history
deployagent status deploy.yaml --history
```

#### `rollback` — Revert to previous deploy
```bash
# Revert to last successful deploy
deployagent rollback deploy.yaml

# Revert two deploys back
deployagent rollback deploy.yaml --steps 2
```

---

### Full Deployment Workflow

```bash
# 1. Go to your project
cd /path/to/your-project

# 2. Copy deploy.yaml and fill in your AWS resource names
cp /path/to/Custom-CICD/deploy.yaml ./deploy.yaml

# 3. Tag with current commit
TAG=$(git rev-parse --short HEAD)
sed -i "s/tag: .*/tag: $TAG/" deploy.yaml   # Linux/macOS

# 4. Preview changes
deployagent plan deploy.yaml

# 5. Deploy
deployagent apply deploy.yaml

# 6. Verify health
deployagent status deploy.yaml

# 7. If something goes wrong
deployagent rollback deploy.yaml
```

---

## GitHub Repo Analyzer

AI-powered tool that clones any GitHub repository, analyzes Dockerfiles and Kubernetes manifests for errors, auto-fixes common issues, and commits the fixes to a new branch.

### What It Does

- Clones any public or private GitHub repository
- Builds a RAG (Retrieval-Augmented Generation) search index over the codebase
- Detects errors in Dockerfiles, docker-compose files, and Kubernetes YAML
- Auto-fixes common issues using pattern matching and local AI (Ollama)
- Generates a detailed `ANALYSIS_REPORT.md`
- Commits fixes and pushes to a new branch with a PR link

### Analyzer Setup

#### 1. Install Ollama (local AI — free, no API key needed)

```bash
# Windows
winget install Ollama.Ollama

# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.ai/install.sh | sh
```

Pull the required models:

```bash
ollama pull llama3       # general analysis
ollama pull codellama    # code and Dockerfile analysis
ollama pull mistral      # fast scanning
```

#### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

#### 3. Set up `.env`

```env
GITHUB_TOKEN=ghp_your_github_personal_access_token
```

Create a GitHub token at: https://github.com/settings/tokens (needs `repo` scope)

---

### Running the Analyzer

```bash
python new.py
```

You will be prompted for:
1. GitHub repository URL (e.g. `https://github.com/username/my-project`)
2. Whether to build and run Docker containers after analysis
3. A branch name and commit message for the fixes

The tool will:
1. Clone the repository
2. Scan all Dockerfiles, YAML, and K8s manifests
3. Report all issues with severity levels
4. Auto-fix what it can
5. Save an `ANALYSIS_REPORT.md` in the repo
6. Push fixes to your chosen branch

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `docker: error during connect` | Start Docker Desktop |
| `AccessDeniedException` on AWS | Add missing IAM permissions (see AWS Setup section) |
| `Nothing to deploy` in plan | Change the image tag — same tag = no change detected |
| `Failed to connect to github.com port 443` | Check internet connection, disable VPN |
| `cannot pull with rebase: You have unstaged changes` | Run `git stash` before `git pull` |
| `Ollama not available` | Run `ollama serve` then `ollama pull llama3` |
| `Can't remove temp_repo (Windows)` | Run `Remove-Item -Recurse -Force ./temp_repo` |

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m "Add my feature"`
4. Push the branch: `git push origin feature/my-feature`
5. Open a pull request

---

## License

MIT License — free to use in personal and commercial projects.

---

## Credits

Built by [chiman45](https://github.com/chiman45)

Tools used:
- [Typer](https://typer.tiangolo.com) — CLI framework
- [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) — AWS SDK
- [Rich](https://rich.readthedocs.io) — terminal output
- [Ollama](https://ollama.ai) — local AI models
- [LangChain](https://langchain.com) — RAG implementation
- [ChromaDB](https://www.trychroma.com) — vector storage

---

*Email: chimanesda@gmail.com*
