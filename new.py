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
        self.ollama_model = "llama3"  # Default Ollama model
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
        
        # Check if Ollama is installed and running
        print("🔍 Checking Ollama installation...")
        try:
            ollama_check = requests.get("http://localhost:11434/api/tags", timeout=5)
            if ollama_check.status_code == 200:
                models = ollama_check.json().get('models', [])
                if models:
                    print(f"✅ Ollama is running with {len(models)} models")
                    # Use first available model or llama3
                    available_models = [m['name'] for m in models]
                    if 'llama3:latest' in available_models:
                        self.ollama_model = 'llama3'
                    elif 'codellama:latest' in available_models:
                        self.ollama_model = 'codellama'
                    elif 'mistral:latest' in available_models:
                        self.ollama_model = 'mistral'
                    else:
                        self.ollama_model = available_models[0].split(':')[0]
                    print(f"📦 Using model: {self.ollama_model}")
                else:
                    print("⚠️  No models found. Installing llama3...")
                    subprocess.run(['ollama', 'pull', 'llama3'], check=True)
                    self.ollama_model = 'llama3'
            else:
                raise Exception("Ollama not responding")
        except Exception as e:
            print(f"❌ Ollama not available: {e}")
            print("\n💡 Install Ollama to use local models (no rate limits!):")
            print("   1. Download from: https://ollama.ai")
            print("   2. Install and run: ollama serve")
            print("   3. Pull a model: ollama pull llama3")
            sys.exit(1)
        
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
    
    def analyze_with_ollama(self, file_path: str, content: str, file_type: str) -> Dict[str, Any]:
        """Analyze file using local Ollama model (no rate limits!)"""
        
        # Shorter, more focused prompts for better JSON output
        prompts = {
            'yaml': f"""Analyze this YAML file and return ONLY valid JSON in this exact format:
{{"errors": ["error1", "error2"], "warnings": ["warning1"], "suggestions": ["suggestion1"], "severity": "high"}}

File: {file_path}

Content:
{content}

Check for: syntax errors, missing fields, port issues, indentation. Return ONLY the JSON object.""",
            
            'docker': f"""Analyze this Dockerfile and return ONLY valid JSON in this exact format:
{{"errors": ["error1", "error2"], "warnings": ["warning1"], "suggestions": ["suggestion1"], "severity": "high"}}

File: {file_path}

Content:
{content}

Check for: syntax errors, FROM statement issues, security problems. Return ONLY the JSON object.""",
            
            'k8s': f"""Analyze this Kubernetes manifest and return ONLY valid JSON in this exact format:
{{"errors": ["error1", "error2"], "warnings": ["warning1"], "suggestions": ["suggestion1"], "severity": "high"}}

File: {file_path}

Content:
{content}

Check for: apiVersion, resource specs, missing colons, port numbers. Return ONLY the JSON object."""
        }
        
        try:
            # Use local Ollama model (no rate limits!)
            prompt = prompts.get(file_type, prompts['yaml'])
            
            ollama_url = "http://localhost:11434/api/generate"
            payload = {
                "model": self.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json"
            }
            
            ollama_response = requests.post(ollama_url, json=payload, timeout=120)
            ollama_response.raise_for_status()
            
            response_data = ollama_response.json()
            response_text = response_data['response']
            
            # Remove markdown code blocks if present
            if '```json' in response_text:
                response_text = response_text.split('```json')[1].split('```')[0]
            elif '```' in response_text:
                response_text = response_text.split('```')[1].split('```')[0]
            
            # Parse JSON and validate structure
            result = json.loads(response_text.strip())
            
            # Ensure all required keys exist
            if not isinstance(result, dict):
                result = {"errors": [], "warnings": [], "suggestions": [], "severity": "unknown"}
            
            result.setdefault('errors', [])
            result.setdefault('warnings', [])
            result.setdefault('suggestions', [])
            result.setdefault('severity', 'unknown')
            
            return result
        except Exception as e:
            print(f"⚠️  Ollama analysis failed for {file_path}: {e}")
            return {"errors": [], "warnings": [], "suggestions": [], "severity": "unknown"}
    
    def check_yaml_syntax(self, file_path: str) -> List[str]:
        """Check YAML syntax"""
        errors = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                # Try to parse YAML
                try:
                    docs = list(yaml.safe_load_all(content))
                except yaml.YAMLError as e:
                    errors.append(f"YAML syntax error: {str(e)}")
                    return errors
                
                # Additional structural checks
                lines = content.split('\n')
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    
                    # Check for missing colons after keys
                    if stripped and not stripped.startswith('#') and not stripped.startswith('-'):
                        # If line looks like a key but missing colon
                        if stripped and not ':' in stripped and not stripped.startswith('|') and not stripped.startswith('>'):
                            # Check if next line is indented (suggests missing colon)
                            if i < len(lines):
                                next_line = lines[i] if i < len(lines) else ""
                                if next_line and len(next_line) - len(next_line.lstrip()) > len(line) - len(line.lstrip()):
                                    errors.append(f"Line {i}: Missing colon ':' after key '{stripped}'")
                    
                    # Check for incomplete port definitions
                    if 'port' in stripped and ':' in stripped:
                        parts = stripped.split(':')
                        if len(parts) == 2 and parts[1].strip() == '':
                            errors.append(f"Line {i}: Incomplete port definition - missing port number")
                    
                    # Check for incomplete apiVersion
                    if stripped.startswith('apiVersion') and ':' in stripped:
                        parts = stripped.split(':', 1)
                        if len(parts) == 2:
                            version = parts[1].strip()
                            if not version:
                                errors.append(f"Line {i}: Missing apiVersion value")
                            elif ' ' in version and '/' not in version:
                                errors.append(f"Line {i}: Invalid apiVersion format - missing '/' (should be 'group/version')")
                
        except Exception as e:
            errors.append(f"Could not check YAML: {str(e)}")
        
        return errors
    
    def check_dockerfile_syntax(self, file_path: str) -> List[str]:
        """Check Dockerfile for common syntax errors"""
        errors = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for i, line in enumerate(lines, 1):
                    # Check for FROM statement issues
                    if line.strip().startswith('FROM') and not line.strip().endswith('\\'):
                        # Check if FROM statement is incomplete
                        if line.strip() == 'FROM':
                            errors.append(f"Line {i}: FROM statement is incomplete - missing base image")
                        # Check if FROM has proper format
                        parts = line.strip().split()
                        if len(parts) < 2:
                            errors.append(f"Line {i}: FROM requires at least one argument (base image)")
        except Exception as e:
            errors.append(f"Could not check Dockerfile: {e}")
        return errors
    
    def fix_yaml_file(self, file_path: str) -> bool:
        """Fix common YAML syntax errors"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            fixed = False
            new_lines = []
            
            for i, line in enumerate(lines):
                original_line = line
                stripped = line.strip()
                
                # Fix missing colon after 'selector'
                if stripped == 'selector':
                    indent = len(line) - len(line.lstrip())
                    new_lines.append(' ' * indent + 'selector:\n')
                    fixed = True
                    print(f"      ✅ Fixed line {i+1}: Added colon after 'selector'")
                    continue
                
                # Fix apiVersion with space (apps/v1 written as apps /v1)
                if 'apiVersion' in line and ':' in line:
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        key = parts[0]
                        value = parts[1].strip()
                        # Remove spaces before /
                        if ' /' in value:
                            value = value.replace(' /', '/')
                            indent = len(line) - len(line.lstrip())
                            new_lines.append(' ' * indent + f'apiVersion: {value}\n')
                            fixed = True
                            print(f"      ✅ Fixed line {i+1}: Corrected apiVersion format")
                            continue
                
                # Fix incomplete port in probes
                if 'port:' in line and line.rstrip().endswith('port:'):
                    # Check if this is in a probe context
                    context_lines = ''.join(lines[max(0, i-5):i])
                    if 'Probe' in context_lines:
                        # Look for containerPort in nearby lines
                        port_found = None
                        for j in range(max(0, i-10), min(len(lines), i+10)):
                            if 'containerPort:' in lines[j]:
                                port_num = lines[j].split('containerPort:')[1].strip()
                                if port_num.isdigit():
                                    port_found = port_num
                                    break
                        
                        if port_found:
                            indent = len(line) - len(line.lstrip())
                            new_lines.append(' ' * indent + f'port: {port_found}\n')
                            fixed = True
                            print(f"      ✅ Fixed line {i+1}: Added port number {port_found}")
                            continue
                
                new_lines.append(original_line)
            
            if fixed:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                return True
            
            return False
            
        except Exception as e:
            print(f"      ⚠️  Could not fix YAML file: {e}")
            return False
        """Fix common Dockerfile syntax errors"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            fixed = False
            new_lines = []
            i = 0
            
            while i < len(lines):
                line = lines[i]
                
                # Fix split FROM statements
                if line.strip() == 'FROM' and i + 1 < len(lines):
                    # Check if next non-empty line has the base image
                    next_line_idx = i + 1
                    while next_line_idx < len(lines) and not lines[next_line_idx].strip():
                        next_line_idx += 1
                    
                    if next_line_idx < len(lines):
                        base_image = lines[next_line_idx].strip()
                        # Combine FROM with base image
                        new_lines.append(f"FROM {base_image}\n")
                        fixed = True
                        print(f"      ✅ Fixed: Combined 'FROM' with '{base_image}'")
                        # Skip the empty lines and base image line
                        i = next_line_idx + 1
                        continue
                
                new_lines.append(line)
                i += 1
            
            if fixed:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                return True
            
            return False
            
        except Exception as e:
            print(f"      ⚠️  Could not fix Dockerfile: {e}")
            return False
    
    def fix_with_gemini(self, file_path: str, errors: List[str], file_type: str) -> bool:
        """Use local Ollama model to fix file errors (no rate limits!)"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
            
            # Create fix prompt
            error_list = '\n'.join([f"- {error}" for error in errors])
            
            # Enhanced prompt for better results
            if file_type == 'docker':
                specific_instructions = """
- Each command (FROM, RUN, COPY, etc.) must be on a single line
- Do not split FROM statements across multiple lines
- Ensure proper Dockerfile syntax
- Keep comments on their own lines"""
            elif file_type in ['yaml', 'k8s']:
                specific_instructions = """
- Maintain proper YAML indentation (2 spaces per level)
- Ensure all keys have colons (:)
- Port numbers must be valid integers
- No trailing spaces after colons without values"""
            else:
                specific_instructions = "- Follow best practices for this file type"
            
            prompt = f"""Fix the following errors in this {file_type} file. Return ONLY the corrected file content, nothing else.

ERRORS TO FIX:
{error_list}

SPECIFIC REQUIREMENTS:{specific_instructions}

ORIGINAL FILE CONTENT:
{original_content}

CRITICAL: 
1. Return ONLY the fixed file content
2. Do NOT add explanations, comments, or markdown
3. Do NOT wrap in code blocks or backticks
4. Preserve the original structure as much as possible"""
            
            # Use local Ollama model (no rate limits!)
            ollama_url = "http://localhost:11434/api/generate"
            payload = {
                "model": self.ollama_model,
                "prompt": prompt,
                "stream": False
            }
            
            ollama_response = requests.post(ollama_url, json=payload, timeout=120)
            ollama_response.raise_for_status()
            
            response_data = ollama_response.json()
            fixed_content = response_data['response'].strip()
            
            # Remove markdown code blocks if present
            if '```' in fixed_content:
                # Extract content between code fences
                lines = fixed_content.split('\n')
                in_code_block = False
                clean_lines = []
                
                for line in lines:
                    if line.strip().startswith('```'):
                        in_code_block = not in_code_block
                        continue
                    if in_code_block or not any(lines[0].strip().startswith('```')):
                        clean_lines.append(line)
                
                fixed_content = '\n'.join(clean_lines)
            
            # Post-process Dockerfile to ensure FROM statements are not split
            if file_type == 'docker':
                fixed_lines = fixed_content.split('\n')
                final_lines = []
                i = 0
                
                while i < len(fixed_lines):
                    line = fixed_lines[i]
                    
                    # If we find a standalone FROM, merge with next non-empty line
                    if line.strip() == 'FROM' and i + 1 < len(fixed_lines):
                        next_idx = i + 1
                        while next_idx < len(fixed_lines) and not fixed_lines[next_idx].strip():
                            next_idx += 1
                        
                        if next_idx < len(fixed_lines):
                            # Merge FROM with the base image
                            base_image = fixed_lines[next_idx].strip()
                            final_lines.append(f"FROM {base_image}")
                            i = next_idx + 1
                            continue
                    
                    final_lines.append(line)
                    i += 1
                
                fixed_content = '\n'.join(final_lines)
            
            # Write fixed content
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(fixed_content)
            
            return True
            
        except Exception as e:
            print(f"      ⚠️  Ollama fix failed: {e}")
            return False
    
    def fix_discovered_issues(self, analysis_results, files_found) -> List[str]:
        """Fix issues found during analysis"""
        fixed_files = []
        
        print(f"\n{'='*60}")
        print("FIXING DISCOVERED ISSUES")
        print(f"{'='*60}")
        
        for result in analysis_results:
            file_path = os.path.join(self.local_repo_path, result['file'])
            
            # Skip if no errors to fix
            if not result['analysis']['errors']:
                continue
            
            print(f"\n📝 Attempting to fix {result['file']}...")
            print(f"   Errors found: {len(result['analysis']['errors'])}")
            
            # Try pattern-based fixes first
            pattern_fixed = False
            
            # Fix Dockerfile issues
            if result['type'] == 'docker':
                has_from_error = any('FROM' in str(error) for error in result['analysis']['errors'])
                if has_from_error:
                    pattern_fixed = self.fix_dockerfile(file_path)
            
            # Fix YAML/K8s issues
            elif result['type'] in ['yaml', 'k8s']:
                errors = result['analysis']['errors']
                has_fixable_error = any(
                    'Missing colon' in str(error) or 
                    'apiVersion' in str(error) or 
                    'port' in str(error)
                    for error in errors
                )
                
                if has_fixable_error:
                    pattern_fixed = self.fix_yaml_file(file_path)
            
            # If pattern-based fix worked
            if pattern_fixed:
                fixed_files.append(result['file'])
                print(f"   ✅ Fixed using pattern matching")
                continue
            
            # If pattern-based fix failed, use Gemini AI
            print(f"   🤖 Trying AI-powered fix...")
            time.sleep(2)  # Rate limiting
            
            if self.fix_with_gemini(file_path, result['analysis']['errors'], result['type']):
                fixed_files.append(result['file'])
                print(f"   ✅ Fixed using Gemini AI")
            else:
                print(f"   ❌ Could not fix {result['file']}")
        
        if fixed_files:
            print(f"\n✅ Successfully fixed {len(fixed_files)} file(s)")
            for file in fixed_files:
                print(f"   - {file}")
        else:
            print(f"\n⚠️  No files could be fixed")
        
        return fixed_files
    
    def analyze_yaml_static(self, file_path: str, content: str) -> Dict[str, Any]:
        """Static YAML analysis without API calls"""
        errors = []
        warnings = []
        suggestions = []
        
        try:
            # First check for basic YAML parsing
            try:
                docs = list(yaml.safe_load_all(content))
            except yaml.YAMLError as e:
                errors.append(f"YAML parsing failed: {str(e)}")
                return {
                    "errors": errors,
                    "warnings": warnings,
                    "suggestions": suggestions,
                    "severity": "critical"
                }
            
            # Line-by-line analysis for common issues
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                
                # Check for missing colons (common error)
                if stripped.startswith('selector') and ':' not in stripped:
                    errors.append(f"Line {i}: Missing colon after 'selector'")
                
                # Check for incomplete port definitions
                if 'port:' in line:
                    parts = line.split('port:', 1)
                    if len(parts) == 2:
                        port_value = parts[1].strip()
                        if not port_value or not port_value[0].isdigit():
                            errors.append(f"Line {i}: Missing or invalid port number after 'port:'")
                
                # Check for space issues in apiVersion
                if stripped.startswith('apiVersion') and ' ' in stripped:
                    if stripped.count(':') > 0:
                        version_part = stripped.split(':', 1)[1].strip()
                        if ' ' in version_part:
                            errors.append(f"Line {i}: apiVersion has invalid format - contains spaces")
            
            # Parse K8s specific checks if YAML is valid
            for doc in docs:
                if not doc:
                    continue
                    
                # K8s specific checks
                if 'kind' in doc:
                    # Check apiVersion format
                    if 'apiVersion' in doc:
                        api_version = str(doc['apiVersion'])
                        if ' ' in api_version:
                            errors.append(f"apiVersion '{api_version}' is invalid - contains spaces")
                    
                    # Check for resource limits
                    if doc['kind'] == 'Deployment':
                        if 'spec' in doc and 'template' in doc['spec']:
                            containers = doc['spec']['template']['spec'].get('containers', [])
                            for container in containers:
                                if 'resources' not in container:
                                    warnings.append(f"Container '{container.get('name', 'unknown')}' missing resource limits")
                                else:
                                    # Check if resources are properly defined
                                    resources = container.get('resources', {})
                                    if resources and isinstance(resources, dict):
                                        if 'requests' in resources:
                                            suggestions.append(f"Container '{container.get('name', 'unknown')}' has resource requests defined")
                                
                                # Check for security context
                                if 'securityContext' not in container:
                                    suggestions.append(f"Consider adding securityContext for container '{container.get('name', 'unknown')}'")
                                
                                # Check probes
                                if 'livenessProbe' in container:
                                    probe = container['livenessProbe']
                                    if isinstance(probe, dict) and 'httpGet' in probe:
                                        http_get = probe['httpGet']
                                        if 'port' not in http_get or not http_get.get('port'):
                                            errors.append(f"livenessProbe in container '{container.get('name', 'unknown')}' has missing or invalid port")
                    
                    # Check for security context at pod level
                    if doc['kind'] in ['Deployment', 'Pod', 'DaemonSet', 'StatefulSet']:
                        spec = doc.get('spec', {})
                        if doc['kind'] == 'Deployment':
                            spec = doc['spec'].get('template', {}).get('spec', {})
                        
                        if 'securityContext' not in spec:
                            warnings.append("Missing pod-level security context")
        
        except Exception as e:
            errors.append(f"Static analysis error: {str(e)}")
        
        severity = "critical" if errors else ("high" if warnings else "low")
        
        return {
            "errors": errors,
            "warnings": warnings,
            "suggestions": suggestions,
            "severity": severity
        }
    
    def analyze_repository(self):
        """Analyze all YAML, Docker, and K8s files"""
        print("\n🔍 Analyzing repository files...")
        
        analysis_results = []
        files_found = []
        files_to_analyze = []
        
        # First pass: collect all files
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
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            if 'apiVersion:' in content or 'kind:' in content:
                                file_type = 'k8s'
                    except:
                        pass
                elif file.lower() == 'dockerfile' or file.endswith('.dockerfile'):
                    file_type = 'docker'
                    files_found.append(('Dockerfile', relative_path))
                
                if file_type:
                    files_to_analyze.append((file_path, relative_path, file_type))
        
        # Second pass: analyze with delays
        for i, (file_path, relative_path, file_type) in enumerate(files_to_analyze):
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
            
            # Syntax check for Dockerfile
            if file_type == 'docker':
                dockerfile_errors = self.check_dockerfile_syntax(file_path)
                if dockerfile_errors:
                    self.errors_found.extend(dockerfile_errors)
                    analysis_results.append({
                        'file': relative_path,
                        'type': file_type,
                        'analysis': {
                            'errors': dockerfile_errors,
                            'warnings': [],
                            'suggestions': [],
                            'severity': 'high'
                        }
                    })
            
            # Static analysis for K8s files (doesn't use API)
            if file_type == 'k8s':
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    static_analysis = self.analyze_yaml_static(file_path, content)
                    
                    if static_analysis and (static_analysis['errors'] or static_analysis['warnings']):
                        analysis_results.append({
                            'file': relative_path,
                            'type': file_type,
                            'analysis': static_analysis
                        })
                        
                        for error in static_analysis['errors']:
                            self.errors_found.append(f"[{relative_path}] ERROR: {error}")
                        for warning in static_analysis['warnings']:
                            self.errors_found.append(f"[{relative_path}] WARNING: {warning}")
                
                except Exception as e:
                    print(f"  ⚠️  Error in static analysis {relative_path}: {e}")
            
            # No delay needed for local Ollama (no rate limits!)
            
            # Deep analysis with local Ollama model
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                ollama_analysis = self.analyze_with_ollama(relative_path, content, file_type)
                
                if ollama_analysis and (ollama_analysis['errors'] or ollama_analysis['warnings']):
                    # Check if we already have results for this file
                    existing = next((r for r in analysis_results if r['file'] == relative_path), None)
                    
                    if existing:
                        # Merge results
                        existing['analysis']['errors'].extend(ollama_analysis['errors'])
                        existing['analysis']['warnings'].extend(ollama_analysis['warnings'])
                        existing['analysis']['suggestions'].extend(ollama_analysis['suggestions'])
                    else:
                        analysis_results.append({
                            'file': relative_path,
                            'type': file_type,
                            'analysis': ollama_analysis
                        })
                    
                    # Add to errors list
                    for error in ollama_analysis['errors']:
                        self.errors_found.append(f"[{relative_path}] ERROR: {error}")
                    for warning in ollama_analysis['warnings']:
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
                print(f"   Type: {result.get('type', 'unknown')}")
                if 'analysis' in result and isinstance(result['analysis'], dict):
                    print(f"   Severity: {result['analysis'].get('severity', 'unknown')}")
                else:
                    print(f"   Severity: unknown")
                    result['analysis'] = {"errors": [], "warnings": [], "suggestions": [], "severity": "unknown"}
                
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
        
        # FIX: Add UTF-8 encoding to handle emoji characters
        with open(fixes_path, 'w', encoding='utf-8') as f:
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
                with open(compose_path, 'r', encoding='utf-8') as f:
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
                                with open(env_path, 'w', encoding='utf-8') as env_file:
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
                    print("   3. Check Dockerfile syntax (look for split FROM statements)")
                    print("   4. Ensure all required environment variables are set")
                    
            except FileNotFoundError:
                print("❌ docker-compose command not found. Please install Docker Compose.")
            except Exception as e:
                print(f"❌ Docker Compose failed: {e}")
            
            return
        
        # Fallback: Search for individual Dockerfiles
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
    
    def commit_and_push(self, analysis_results, files_found, fixed_files):
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
            if fixed_files:
                commit_message = f"Fix {len(fixed_files)} file(s) and add analysis report"
            else:
                commit_message = f"Add analysis report - {len(files_found)} files checked"
        
        try:
            # Create and checkout new branch
            print(f"\n🔀 Creating branch: {branch_name}")
            new_branch = self.repo.create_head(branch_name)
            new_branch.checkout()
            
            # Add the analysis report and fixed files
            files_to_add = ['ANALYSIS_REPORT.md']
            if fixed_files:
                files_to_add.extend(fixed_files)
                print(f"📝 Adding fixed files: {', '.join(fixed_files)}")
            
            self.repo.index.add(files_to_add)
            
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
            
            # Fix discovered issues automatically
            fixed_files = []
            try:
                if analysis_results:
                    print("\n🔧 Auto-fixing discovered issues...")
                    fixed_files = self.fix_discovered_issues(analysis_results, files_found)
            except Exception as e:
                print(f"⚠️  Error during fixing: {e}")
            
            # Ask about Docker
            try:
                if self.docker_client:
                    run_docker = input("\n🐳 Do you want to build and run Docker image? (yes/no): ").strip().lower()
                    if run_docker == 'yes':
                        self.build_and_run_docker()
            except Exception as e:
                print(f"⚠️  Docker error: {e}")
            
            # Always commit if there are fixes or analysis results
            try:
                if analysis_results or fixed_files or files_found:
                    self.commit_and_push(analysis_results, files_found, fixed_files)
            except Exception as e:
                print(f"⚠️  Commit error: {e}")
            
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