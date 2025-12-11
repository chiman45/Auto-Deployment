import os
import sys
import subprocess
import webbrowser
import time
import yaml
import json
import shutil
import stat
from pathlib import Path
from typing import List, Dict, Any
import google.generativeai as genai
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from git import Repo
import docker
import requests
from dotenv import load_dotenv

def force_remove_readonly(func, path, exc_info):
    """Error handler for Windows readonly files"""
    if not os.access(path, os.W_OK):
        os.chmod(path, stat.S_IWUSR | stat.S_IWRITE)
        func(path)
    else:
        raise

class GitHubRepoAnalyzer:
    def __init__(self):
        self.repo_url = None
        self.github_token = None
        self.gemini_api_key = None
        self.local_repo_path = "./temp_repo"
        self.repo = None
        self.vectorstore = None
        self.docker_client = None
        self.errors_found = []
        
    def setup(self):
        """Initial setup - get credentials and repo info"""
        print("=" * 60)
        print("GitHub Repository Analyzer Agent")
        print("=" * 60)
        
        # Load environment variables
        load_dotenv()
        
        # Get Gemini API key from .env
        self.gemini_api_key = os.getenv('GEMINI_API_KEY')
        if not self.gemini_api_key:
            print("❌ GEMINI_API_KEY not found in .env file!")
            print("   Please create a .env file with: GEMINI_API_KEY=your_api_key_here")
            sys.exit(1)
        
        print("✅ Gemini API key loaded from .env")
        
        # Configure Gemini
        genai.configure(api_key=self.gemini_api_key)
        
        # Get GitHub token from user
        self.github_token = input("\nEnter your GitHub Personal Access Token: ").strip()
        if not self.github_token:
            print("❌ GitHub token is required!")
            sys.exit(1)
        
        # Get repo URL
        self.repo_url = input("\nEnter GitHub repository URL: ").strip()
        if not self.repo_url:
            print("❌ Repository URL is required!")
            sys.exit(1)
        
        # Initialize Docker client
        try:
            self.docker_client = docker.from_env()
            print("✅ Docker client initialized")
        except Exception as e:
            print(f"⚠️  Docker not available: {e}")
            self.docker_client = None
    
    def clone_repository(self):
        """Clone the GitHub repository"""
        print(f"\n🔄 Cloning repository: {self.repo_url}")
        
        # Check if repo already exists
        if os.path.exists(self.local_repo_path):
            try:
                # Try to use existing repo
                self.repo = Repo(self.local_repo_path)
                
                # Check if it's the same repo
                remote_url = self.repo.remotes.origin.url
                # Clean URLs for comparison
                clean_remote = remote_url.replace('https://', '').replace('.git', '').split('@')[-1]
                clean_input = self.repo_url.replace('https://', '').replace('.git', '')
                
                if clean_remote != clean_input:
                    print(f"⚠️  Different repository detected. Removing old repo...")
                    try:
                        shutil.rmtree(self.local_repo_path, onerror=force_remove_readonly)
                        print("✅ Old repository removed")
                    except Exception as rm_err:
                        print(f"⚠️  Could not auto-remove: {rm_err}")
                        print("   Continuing with fresh clone anyway...")
                else:
                    print("✅ Using existing repository")
                    
                    # Reset any local changes
                    print("🔄 Resetting local changes...")
                    self.repo.git.reset('--hard')
                    
                    # Fetch all branches and tags
                    print("🔄 Fetching latest changes...")
                    origin = self.repo.remote('origin')
                    origin.fetch()
                    
                    # Pull latest changes from current branch
                    print("🔄 Pulling latest updates...")
                    origin.pull()
                    
                    print("✅ Repository updated to latest version")
                    return
                    
            except Exception as e:
                # If existing repo is invalid, try to remove it
                print(f"⚠️  Issue with existing repo: {e}")
                print("🧹 Attempting to remove old repository...")
                
                try:
                    if os.path.exists(self.local_repo_path):
                        shutil.rmtree(self.local_repo_path, onerror=force_remove_readonly)
                        print("✅ Old repository removed")
                except Exception as rm_err:
                    print(f"⚠️  Could not auto-remove: {rm_err}")
                    print("   Will try fresh clone anyway...")
        
        # Ensure path doesn't exist before cloning
        if os.path.exists(self.local_repo_path):
            print("🧹 Cleaning up existing path...")
            try:
                shutil.rmtree(self.local_repo_path, onerror=force_remove_readonly)
                time.sleep(1)  # Wait a moment for Windows
            except Exception as e:
                print(f"❌ Could not remove temp_repo: {e}")
                print("\n💡 MANUAL FIX: Delete the 'temp_repo' folder manually:")
                print(f"   1. Close any programs using files in temp_repo")
                print(f"   2. Delete: {os.path.abspath(self.local_repo_path)}")
                print(f"   3. Or run in PowerShell as Admin:")
                print(f"      Remove-Item -Recurse -Force '{os.path.abspath(self.local_repo_path)}'")
                sys.exit(1)
        
        # Clone fresh repository
        auth_url = self.repo_url.replace("https://", f"https://{self.github_token}@")
        
        try:
            print("📥 Cloning fresh repository...")
            self.repo = Repo.clone_from(auth_url, self.local_repo_path)
            print("✅ Repository cloned successfully")
        except Exception as e:
            print(f"❌ Failed to clone repository: {e}")
            print("\n💡 Try manually deleting the './temp_repo' folder and run again")
            sys.exit(1)
    
    def build_rag_index(self):
        """Build RAG index using ChromaDB"""
        print("\n🔄 Building RAG index...")
        
        documents = []
        file_extensions = ['.py', '.yaml', '.yml', '.json', '.md', '.txt', '.sh', 
                          '.dockerfile', '.Dockerfile', '.env']
        
        for root, dirs, files in os.walk(self.local_repo_path):
            # Skip .git directory
            if '.git' in root:
                continue
            
            for file in files:
                if any(file.endswith(ext) or file.lower() == 'dockerfile' for ext in file_extensions):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                            documents.append({
                                'content': content,
                                'metadata': {'source': file_path, 'filename': file}
                            })
                    except Exception as e:
                        print(f"⚠️  Could not read {file}: {e}")
        
        if not documents:
            print("⚠️  No documents found to index")
            return
        
        # Split documents
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200
        )
        
        texts = []
        metadatas = []
        for doc in documents:
            chunks = text_splitter.split_text(doc['content'])
            texts.extend(chunks)
            metadatas.extend([doc['metadata']] * len(chunks))
        
        # Create embeddings and vectorstore
        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        self.vectorstore = Chroma.from_texts(
            texts=texts,
            embedding=embeddings,
            metadatas=metadatas,
            persist_directory="./chroma_db"
        )
        
        print(f"✅ RAG index built with {len(documents)} documents")
    
    def analyze_with_gemini(self, file_path: str, content: str, file_type: str) -> Dict[str, Any]:
        """Analyze file using Gemini API"""
        
        prompts = {
            'yaml': f"""Analyze this Kubernetes/YAML configuration file for:
1. Syntax errors
2. Logical errors (wrong selectors, missing fields, incorrect resource specs)
3. Security issues (privileged containers, hostPath, etc.)
4. Best practices violations
5. Service label selector mismatches
6. Resource limits and requests issues
7. Advanced cluster-level configuration problems

File: {file_path}
Content:
{content}

Provide detailed analysis in JSON format with: {{"errors": [], "warnings": [], "suggestions": [], "severity": "low/medium/high/critical"}}""",
            
            'docker': f"""Analyze this Dockerfile for:
1. Syntax errors
2. Security vulnerabilities (running as root, exposed secrets, outdated base images)
3. Build optimization issues
4. Best practices violations
5. Logical errors in commands
6. Port exposure issues

File: {file_path}
Content:
{content}

Provide detailed analysis in JSON format with: {{"errors": [], "warnings": [], "suggestions": [], "severity": "low/medium/high/critical"}}""",
            
            'k8s': f"""Analyze this Kubernetes manifest for:
1. API version compatibility
2. Resource specification errors
3. RBAC and security issues
4. Network policy problems
5. Service mesh configuration
6. Ingress/egress rules
7. ConfigMap/Secret references
8. Volume mount issues

File: {file_path}
Content:
{content}

Provide detailed analysis in JSON format with: {{"errors": [], "warnings": [], "suggestions": [], "severity": "low/medium/high/critical"}}"""
        }
        
        try:
            # Use the latest Gemini model: gemini-2.0-flash-exp
            model = genai.GenerativeModel('gemini-2.0-flash-exp')
            prompt = prompts.get(file_type, prompts['yaml'])
            response = model.generate_content(prompt)
            
            # Try to parse JSON from response
            response_text = response.text
            # Remove markdown code blocks if present
            if '```json' in response_text:
                response_text = response_text.split('```json')[1].split('```')[0]
            elif '```' in response_text:
                response_text = response_text.split('```')[1].split('```')[0]
            
            return json.loads(response_text.strip())
        except Exception as e:
            print(f"⚠️  Gemini analysis failed for {file_path}: {e}")
            return {"errors": [], "warnings": [], "suggestions": [], "severity": "unknown"}
    
    def check_yaml_syntax(self, file_path: str) -> List[str]:
        """Check YAML syntax"""
        errors = []
        try:
            with open(file_path, 'r') as f:
                yaml.safe_load_all(f)
        except yaml.YAMLError as e:
            errors.append(f"YAML syntax error in {file_path}: {str(e)}")
        return errors
    
    def analyze_repository(self):
        """Analyze all YAML, Docker, and K8s files"""
        print("\n🔍 Analyzing repository files...")
        
        analysis_results = []
        files_found = []
        
        for root, dirs, files in os.walk(self.local_repo_path):
            if '.git' in root:
                continue
            
            for file in files:
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, self.local_repo_path)
                
                # Determine file type
                file_type = None
                if file.endswith(('.yaml', '.yml')):
                    file_type = 'yaml'
                    files_found.append(('YAML', relative_path))
                    # Check if it's a K8s manifest
                    try:
                        with open(file_path, 'r') as f:
                            content = f.read()
                            if 'apiVersion:' in content or 'kind:' in content:
                                file_type = 'k8s'
                    except:
                        pass
                elif file.lower() == 'dockerfile' or file.endswith('.dockerfile'):
                    file_type = 'docker'
                    files_found.append(('Dockerfile', relative_path))
                
                if file_type:
                    print(f"  📄 Analyzing: {relative_path}")
                    
                    # Basic syntax check for YAML
                    if file_type in ['yaml', 'k8s']:
                        syntax_errors = self.check_yaml_syntax(file_path)
                        if syntax_errors:
                            self.errors_found.extend(syntax_errors)
                            analysis_results.append({
                                'file': relative_path,
                                'type': file_type,
                                'analysis': {
                                    'errors': syntax_errors,
                                    'warnings': [],
                                    'suggestions': [],
                                    'severity': 'high'
                                }
                            })
                    
                    # Deep analysis with Gemini (with retry logic)
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        
                        gemini_analysis = self.analyze_with_gemini(relative_path, content, file_type)
                        
                        if gemini_analysis and (gemini_analysis['errors'] or gemini_analysis['warnings']):
                            analysis_results.append({
                                'file': relative_path,
                                'type': file_type,
                                'analysis': gemini_analysis
                            })
                            
                            # Add to errors list
                            for error in gemini_analysis['errors']:
                                self.errors_found.append(f"[{relative_path}] ERROR: {error}")
                            for warning in gemini_analysis['warnings']:
                                self.errors_found.append(f"[{relative_path}] WARNING: {warning}")
                    
                    except Exception as e:
                        print(f"  ⚠️  Error analyzing {relative_path}: {e}")
        
        # Print files found summary
        print(f"\n{'='*60}")
        print("FILES DISCOVERED")
        print(f"{'='*60}")
        if files_found:
            for file_type, file_path in files_found:
                print(f"  {file_type}: {file_path}")
        else:
            print("  ⚠️  No YAML, Docker, or K8s files found")
        
        # Print analysis summary
        print(f"\n{'='*60}")
        print("ANALYSIS SUMMARY")
        print(f"{'='*60}")
        
        if analysis_results:
            for result in analysis_results:
                print(f"\n📄 File: {result['file']}")
                print(f"   Type: {result['type']}")
                print(f"   Severity: {result['analysis']['severity']}")
                
                if result['analysis']['errors']:
                    print(f"   ❌ Errors ({len(result['analysis']['errors'])}):")
                    for error in result['analysis']['errors'][:3]:  # Show first 3
                        print(f"      - {error}")
                
                if result['analysis']['warnings']:
                    print(f"   ⚠️  Warnings ({len(result['analysis']['warnings'])}):")
                    for warning in result['analysis']['warnings'][:3]:  # Show first 3
                        print(f"      - {warning}")
                
                if result['analysis']['suggestions']:
                    print(f"   💡 Suggestions ({len(result['analysis']['suggestions'])}):")
                    for suggestion in result['analysis']['suggestions'][:2]:  # Show first 2
                        print(f"      - {suggestion}")
        else:
            if files_found:
                print("\n✅ All files analyzed - No critical issues found!")
            else:
                print("\n⚠️  No analyzable files found in repository")
        
        return analysis_results
    
    def create_fixes_file(self, analysis_results, files_found):
        """Create a comprehensive fixes file"""
        fixes_path = os.path.join(self.local_repo_path, "ANALYSIS_REPORT.md")
        
        with open(fixes_path, 'w') as f:
            f.write("# Repository Analysis Report\n\n")
            f.write(f"Generated by GitHub Repo Analyzer Agent\n\n")
            
            f.write("## Files Discovered\n\n")
            if files_found:
                for file_type, file_path in files_found:
                    f.write(f"- **{file_type}**: `{file_path}`\n")
            else:
                f.write("No YAML, Docker, or K8s files found.\n")
            
            f.write("\n## Summary\n\n")
            f.write(f"- Total files analyzed: {len(analysis_results)}\n")
            f.write(f"- Total issues found: {len(self.errors_found)}\n\n")
            
            if analysis_results:
                f.write("## Detailed Analysis\n\n")
                
                for result in analysis_results:
                    f.write(f"### {result['file']}\n\n")
                    f.write(f"**Type:** {result['type']}  \n")
                    f.write(f"**Severity:** {result['analysis']['severity']}  \n\n")
                    
                    if result['analysis']['errors']:
                        f.write("#### ❌ Errors\n\n")
                        for error in result['analysis']['errors']:
                            f.write(f"- {error}\n")
                        f.write("\n")
                    
                    if result['analysis']['warnings']:
                        f.write("#### ⚠️ Warnings\n\n")
                        for warning in result['analysis']['warnings']:
                            f.write(f"- {warning}\n")
                        f.write("\n")
                    
                    if result['analysis']['suggestions']:
                        f.write("#### 💡 Suggestions\n\n")
                        for suggestion in result['analysis']['suggestions']:
                            f.write(f"- {suggestion}\n")
                        f.write("\n")
                    
                    f.write("---\n\n")
            else:
                f.write("## Result\n\n")
                f.write("✅ All files passed analysis with no critical issues detected.\n\n")
        
        print(f"\n✅ Analysis report saved to: ANALYSIS_REPORT.md")
    
    def build_and_run_docker(self):
        """Build and run Docker image if Dockerfile exists"""
        if not self.docker_client:
            print("\n⚠️  Docker not available, skipping build and run")
            return
        
        # Check for docker-compose.yml first (check root level explicitly)
        compose_file = os.path.join(self.local_repo_path, 'docker-compose.yml')
        compose_yaml = os.path.join(self.local_repo_path, 'docker-compose.yaml')
        
        print(f"\n🔍 Searching for Docker files in: {self.local_repo_path}")
        print(f"   Checking: {compose_file}")
        print(f"   Exists: {os.path.exists(compose_file)}")
        
        if os.path.exists(compose_file) or os.path.exists(compose_yaml):
            compose_path = compose_file if os.path.exists(compose_file) else compose_yaml
            print(f"\n🐳 Found docker-compose.yml!")
            print(f"📁 Location: {os.path.relpath(compose_path, self.local_repo_path)}")
            
            # Check for missing .env files and create them
            print("\n🔍 Checking for required .env files...")
            
            # Read docker-compose.yml to find env_file references
            try:
                with open(compose_path, 'r') as f:
                    compose_content = f.read()
                    
                # Check for common .env file locations
                env_locations = [
                    os.path.join(self.local_repo_path, '.env'),
                    os.path.join(self.local_repo_path, 'Frontend', '.env'),
                    os.path.join(self.local_repo_path, 'Backend', '.env'),
                ]
                
                for env_path in env_locations:
                    if not os.path.exists(env_path):
                        # Check if this .env is actually needed
                        dir_name = os.path.basename(os.path.dirname(env_path))
                        if dir_name in compose_content or '.env' in compose_content:
                            print(f"   ⚠️  Missing: {os.path.relpath(env_path, self.local_repo_path)}")
                            
                            # Create empty .env file
                            create_env = input(f"      Create empty .env file? (yes/no): ").strip().lower()
                            if create_env == 'yes':
                                os.makedirs(os.path.dirname(env_path), exist_ok=True)
                                with open(env_path, 'w') as env_file:
                                    env_file.write("# Environment variables\n")
                                    env_file.write("# Add your configuration here\n")
                                print(f"      ✅ Created: {os.path.relpath(env_path, self.local_repo_path)}")
                            else:
                                print(f"      ⚠️  Docker Compose may fail without this file")
            except Exception as e:
                print(f"   ⚠️  Could not check for .env files: {e}")
            
            try:
                # Use docker-compose to build and run
                print("\n🔨 Building services with docker-compose...")
                
                result = subprocess.run(
                    ['docker-compose', 'up', '-d', '--build'],
                    cwd=self.local_repo_path,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode == 0:
                    print("✅ Docker Compose services built and running!")
                    print("\n📋 Running containers:")
                    
                    # List running containers
                    list_result = subprocess.run(
                        ['docker-compose', 'ps'],
                        cwd=self.local_repo_path,
                        capture_output=True,
                        text=True
                    )
                    print(list_result.stdout)
                    
                    # Try to detect and open ports
                    print("\n🌐 Attempting to open application...")
                    time.sleep(5)
                    
                    # Common ports to try
                    ports = [3000, 8080, 5000, 80, 4200, 8000]
                    opened = False
                    for port in ports:
                        try:
                            response = requests.get(f'http://localhost:{port}', timeout=2)
                            if response.status_code < 500:  # Any valid response
                                print(f"✅ Service found on port {port}")
                                webbrowser.open(f'http://localhost:{port}')
                                opened = True
                                break
                        except:
                            continue
                    
                    if not opened:
                        print("⚠️  Could not auto-detect port. Check docker-compose.yml for port mappings.")
                        print("   Try: http://localhost:3000 or http://localhost:8080")
                    
                    print("\n✅ Docker Compose services are running!")
                    print("💡 To stop: cd temp_repo && docker-compose down")
                    
                    # Ask if user wants to stop now
                    stop = input("\n🛑 Stop containers now? (yes/no): ").strip().lower()
                    if stop == 'yes':
                        subprocess.run(['docker-compose', 'down'], cwd=self.local_repo_path)
                        print("✅ Containers stopped")
                else:
                    print(f"❌ Docker Compose failed:")
                    print(result.stderr)
                    print(result.stdout)
                    print("\n💡 Common fixes:")
                    print("   1. Create missing .env files")
                    print("   2. Check docker-compose.yml syntax")
                    print("   3. Ensure all required environment variables are set")
                    
            except FileNotFoundError:
                print("❌ docker-compose command not found. Please install Docker Compose.")
            except Exception as e:
                print(f"❌ Docker Compose failed: {e}")
            
            return
        
        # Fallback: Search for individual Dockerfiles (don't skip any folders except .git)
        print("\n🔍 No docker-compose.yml found, searching for Dockerfiles...")
        dockerfiles = []
        
        for root, dirs, files in os.walk(self.local_repo_path):
            # Only skip .git folder
            if '.git' in root:
                continue
            
            # Debug: show what we're checking
            rel_root = os.path.relpath(root, self.local_repo_path)
            if rel_root != '.':
                print(f"   Checking: {rel_root}/")
            
            for file in files:
                if file.lower() == 'dockerfile' or file.endswith('.dockerfile'):
                    dockerfile_location = os.path.join(root, file)
                    relative_path = os.path.relpath(dockerfile_location, self.local_repo_path)
                    dockerfiles.append((root, relative_path))
                    print(f"   ✅ Found: {relative_path}")
        
        if not dockerfiles:
            print("\n⚠️  No Dockerfile or docker-compose.yml found in repository")
            print(f"\n💡 Debug info:")
            print(f"   Searched in: {self.local_repo_path}")
            print(f"   Directory exists: {os.path.exists(self.local_repo_path)}")
            print(f"   Contents: {os.listdir(self.local_repo_path)[:10]}")
            return
        
        print(f"\n🐳 Found {len(dockerfiles)} Dockerfile(s):")
        for i, (path, rel_path) in enumerate(dockerfiles, 1):
            print(f"   {i}. {rel_path}")
        
        # If multiple Dockerfiles, ask which one to build
        if len(dockerfiles) > 1:
            choice = input(f"\nWhich Dockerfile to build? (1-{len(dockerfiles)}, or 'all'): ").strip().lower()
            
            if choice == 'all':
                print("\n💡 Multiple Dockerfiles detected. Consider using docker-compose.yml instead.")
                return
            
            try:
                idx = int(choice) - 1
                dockerfile_path, dockerfile_rel = dockerfiles[idx]
            except:
                print("❌ Invalid choice")
                return
        else:
            dockerfile_path, dockerfile_rel = dockerfiles[0]
        
        print(f"\n🐳 Building: {dockerfile_rel}")
        
        # Build image
        image_tag = f"repo-analyzer-{os.path.basename(dockerfile_path)}:latest".lower()
        print(f"🔨 Building Docker image: {image_tag}")
        
        try:
            image, build_logs = self.docker_client.images.build(
                path=dockerfile_path,
                tag=image_tag,
                rm=True
            )
            
            # Print build logs
            for log in build_logs:
                if 'stream' in log:
                    print(f"   {log['stream'].strip()}")
            
            print("✅ Docker image built successfully!")
            
            # Run container
            print("🚀 Running Docker container...")
            
            container = self.docker_client.containers.run(
                image_tag,
                detach=True,
                ports={'80/tcp': 8080, '8080/tcp': 8080, '3000/tcp': 8080, '5000/tcp': 8080},
                remove=True
            )
            
            print(f"✅ Container running with ID: {container.short_id}")
            print(f"🌐 Attempting to open website at http://localhost:8080")
            
            # Wait a bit for the service to start
            time.sleep(3)
            
            # Try to open in browser
            webbrowser.open('http://localhost:8080')
            
            print("\n✅ Website should now be open in your browser!")
            print("   Press Ctrl+C to stop the container when done testing...")
            
            # Keep running
            try:
                container.wait()
            except KeyboardInterrupt:
                print("\n🛑 Stopping container...")
                container.stop()
                print("✅ Container stopped")
        
        except Exception as e:
            print(f"❌ Docker build/run failed: {e}")
            print(f"\n💡 Check if Dockerfile is valid or if Docker daemon is running")
    
    def commit_and_push(self, analysis_results, files_found):
        """Commit changes and push to new branch"""
        # Always create report even if no issues
        print(f"\n{'='*60}")
        print("CREATING ANALYSIS REPORT")
        print(f"{'='*60}")
        
        self.create_fixes_file(analysis_results, files_found)
        
        print(f"\n{'='*60}")
        print("COMMIT TO REPOSITORY")
        print(f"{'='*60}")
        
        # Ask for branch name
        branch_name = input("\nEnter new branch name (e.g., 'analysis-report'): ").strip()
        if not branch_name:
            print("❌ Branch name is required!")
            return
        
        # Ask for commit message
        commit_message = input("Enter commit message: ").strip()
        if not commit_message:
            commit_message = f"Add analysis report - {len(files_found)} files checked"
        
        try:
            # Create and checkout new branch
            print(f"\n🔀 Creating branch: {branch_name}")
            new_branch = self.repo.create_head(branch_name)
            new_branch.checkout()
            
            # Add the analysis report
            self.repo.index.add(['ANALYSIS_REPORT.md'])
            
            # Commit
            print(f"💾 Committing changes...")
            self.repo.index.commit(commit_message)
            
            # Push
            print(f"📤 Pushing to remote...")
            origin = self.repo.remote('origin')
            origin.push(f'{branch_name}:{branch_name}')
            
            print(f"\n✅ Successfully pushed to branch: {branch_name}")
            print(f"🔗 Create a pull request to merge these changes")
            
            # Show GitHub PR URL
            repo_url_clean = self.repo_url.replace('.git', '')
            print(f"📝 PR URL: {repo_url_clean}/pull/new/{branch_name}")
        
        except Exception as e:
            print(f"❌ Failed to commit and push: {e}")
    
    def cleanup(self):
        """Cleanup temporary files (cross-platform)"""
        try:
            if os.path.exists(self.local_repo_path):
                print("🧹 Removing temp repository...")
                try:
                    shutil.rmtree(self.local_repo_path, onerror=force_remove_readonly)
                    print("   ✅ Temp repository removed")
                except Exception as e:
                    print(f"   ⚠️  Could not remove temp_repo: {e}")
            
            if os.path.exists("./chroma_db"):
                print("🧹 Removing ChromaDB...")
                shutil.rmtree("./chroma_db", ignore_errors=True)
                print("   ✅ ChromaDB removed")
            
            print("✅ Cleanup complete")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")
    
    def run(self):
        """Main execution flow"""
        try:
            self.setup()
            self.clone_repository()
            self.build_rag_index()
            
            # Store files found for reporting
            files_found = []
            analysis_results = self.analyze_repository()
            
            # Extract files_found from analysis (need to track this)
            for root, dirs, files in os.walk(self.local_repo_path):
                if '.git' in root:
                    continue
                for file in files:
                    relative_path = os.path.relpath(os.path.join(root, file), self.local_repo_path)
                    if file.endswith(('.yaml', '.yml')):
                        files_found.append(('YAML', relative_path))
                    elif file.lower() == 'dockerfile' or file.endswith('.dockerfile'):
                        files_found.append(('Dockerfile', relative_path))
            
            # Ask about Docker
            if self.docker_client:
                run_docker = input("\n🐳 Do you want to build and run Docker image? (yes/no): ").strip().lower()
                if run_docker == 'yes':
                    self.build_and_run_docker()
            
            # Ask about committing - ALWAYS ask, even if no issues
            should_commit = input("\n📝 Do you want to commit the analysis report? (yes/no): ").strip().lower()
            if should_commit == 'yes':
                self.commit_and_push(analysis_results, files_found)
            
            print(f"\n{'='*60}")
            print("✅ ANALYSIS COMPLETE!")
            print(f"{'='*60}")
        
        except KeyboardInterrupt:
            print("\n\n🛑 Operation cancelled by user")
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Cleanup
            print(f"\n{'='*60}")
            cleanup_choice = input("🧹 Cleanup temporary files? (yes/no): ").strip().lower()
            if cleanup_choice == 'yes':
                self.cleanup()
            else:
                print("⚠️  Temporary files kept in ./temp_repo")

if __name__ == "__main__":
    agent = GitHubRepoAnalyzer()
    agent.run()