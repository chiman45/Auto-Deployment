"""
Test script for build_and_run_docker function
Tests Docker functionality by creating a simple test environment
"""

import os
import sys
import shutil
import docker
import time
import webbrowser
import subprocess

class DockerTester:
    def __init__(self):
        self.test_dir = "./test_docker_env"
        self.docker_client = None
        
    def check_docker_running(self):
        """Check if Docker Desktop is running"""
        try:
            # Check if Docker Desktop process is running
            result = subprocess.run(
                ['powershell', '-Command', 'Get-Process "Docker Desktop" -ErrorAction SilentlyContinue'],
                capture_output=True,
                text=True
            )
            return 'Docker Desktop' in result.stdout
        except:
            return False
    
    def start_docker_desktop(self):
        """Try to start Docker Desktop"""
        print("🔄 Attempting to start Docker Desktop...")
        try:
            # Common Docker Desktop installation paths
            docker_paths = [
                r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
                r"C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe",
                os.path.expanduser(r"~\AppData\Local\Docker\Docker Desktop.exe")
            ]
            
            for path in docker_paths:
                if os.path.exists(path):
                    print(f"   Found Docker Desktop at: {path}")
                    subprocess.Popen([path], shell=True)
                    print("   ⏳ Waiting for Docker Desktop to start (30 seconds)...")
                    time.sleep(30)
                    return True
            
            print("   ⚠️  Could not find Docker Desktop executable")
            return False
        except Exception as e:
            print(f"   ⚠️  Failed to start Docker Desktop: {e}")
            return False
    
    def setup_docker_client(self):
        """Initialize Docker client"""
        print("🔍 Checking Docker status...")
        
        # Check if Docker Desktop is running
        if not self.check_docker_running():
            print("⚠️  Docker Desktop is not running")
            start = input("   Would you like to start Docker Desktop? (yes/no): ").strip().lower()
            if start == 'yes':
                if not self.start_docker_desktop():
                    print("❌ Could not start Docker Desktop automatically")
                    print("\n💡 Please start Docker Desktop manually:")
                    print("   1. Open Docker Desktop from Start Menu")
                    print("   2. Wait for it to fully start (whale icon in system tray)")
                    print("   3. Run this script again")
                    return False
        else:
            print("✅ Docker Desktop process is running")
        
        # Try to connect to Docker daemon
        print("🔄 Connecting to Docker daemon...")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Try different connection methods
                try:
                    # Method 1: from_env (default)
                    self.docker_client = docker.from_env()
                except:
                    # Method 2: Explicit named pipe for Windows
                    self.docker_client = docker.DockerClient(base_url='npipe:////./pipe/docker_engine')
                
                # Test connection
                self.docker_client.ping()
                print("✅ Docker client initialized and connected")
                
                # Show Docker info
                info = self.docker_client.info()
                print(f"   Docker version: {info.get('ServerVersion', 'unknown')}")
                print(f"   Containers: {info.get('Containers', 0)} (running: {info.get('ContainersRunning', 0)})")
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"   ⏳ Attempt {attempt + 1}/{max_retries} failed, retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    print(f"❌ Docker not available after {max_retries} attempts: {e}")
                    print("\n💡 Troubleshooting steps:")
                    print("   1. Make sure Docker Desktop is fully started (whale icon steady in system tray)")
                    print("   2. Check Docker Desktop settings:")
                    print("      - Settings > General > 'Expose daemon on tcp://localhost:2375 without TLS'")
                    print("   3. Restart Docker Desktop")
                    print("   4. Try running: docker ps")
                    return False
        
        return False
    
    def cleanup_test_env(self):
        """Remove test directory if exists"""
        if os.path.exists(self.test_dir):
            print(f"🧹 Cleaning up existing test environment...")
            shutil.rmtree(self.test_dir, ignore_errors=True)
            print("   ✅ Cleaned")
    
    def create_test_dockerfile(self):
        """Create a simple test Dockerfile"""
        os.makedirs(self.test_dir, exist_ok=True)
        
        dockerfile_content = """FROM nginx:alpine

# Copy a simple HTML file
RUN echo '<html><body><h1>Docker Test Success!</h1><p>This is a test container running nginx.</p></body></html>' > /usr/share/nginx/html/index.html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
"""
        
        dockerfile_path = os.path.join(self.test_dir, "Dockerfile")
        with open(dockerfile_path, 'w', encoding='utf-8') as f:
            f.write(dockerfile_content)
        
        print(f"✅ Created test Dockerfile at: {dockerfile_path}")
        return dockerfile_path
    
    def create_docker_compose(self):
        """Create a simple docker-compose.yml for testing"""
        compose_content = """version: '3.8'

services:
  web:
    build: .
    ports:
      - "8080:80"
    container_name: test-nginx-container
"""
        
        compose_path = os.path.join(self.test_dir, "docker-compose.yml")
        with open(compose_path, 'w', encoding='utf-8') as f:
            f.write(compose_content)
        
        print(f"✅ Created docker-compose.yml at: {compose_path}")
        return compose_path
    
    def test_docker_compose(self):
        """Test docker-compose functionality"""
        print("\n" + "="*60)
        print("TEST 1: Docker Compose Build & Run")
        print("="*60)
        
        try:
            # Detect docker compose command
            compose_cmd = ['docker', 'compose']
            try:
                test_result = subprocess.run(
                    ['docker', 'compose', 'version'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if test_result.returncode != 0:
                    compose_cmd = ['docker-compose']
            except:
                compose_cmd = ['docker-compose']
            
            print(f"   Using command: {' '.join(compose_cmd)}")
            
            # Stop any existing containers
            print("\n🧹 Cleaning up existing containers...")
            cleanup_result = subprocess.run(
                compose_cmd + ['down'],
                cwd=self.test_dir,
                capture_output=True,
                text=True
            )
            
            # Build and run
            print("🔨 Building with docker-compose...")
            result = subprocess.run(
                compose_cmd + ['up', '-d', '--build'],
                cwd=self.test_dir,
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if result.returncode == 0:
                print("✅ Docker Compose build successful!")
                print("\n📋 Container status:")
                
                # List containers
                list_result = subprocess.run(
                    compose_cmd + ['ps'],
                    cwd=self.test_dir,
                    capture_output=True,
                    text=True
                )
                print(list_result.stdout)
                
                print("\n🌐 Opening http://localhost:8080...")
                time.sleep(2)
                webbrowser.open('http://localhost:8080')
                
                # Wait for user
                input("\n⏸️  Press Enter to stop the container...")
                
                # Stop containers
                subprocess.run(compose_cmd + ['down'], cwd=self.test_dir)
                print("✅ Containers stopped")
                return True
            else:
                print("❌ Docker Compose failed:")
                print(result.stderr)
                print(result.stdout)
                return False
                
        except Exception as e:
            print(f"❌ Test failed: {e}")
            return False
    
    def test_docker_image_pull(self):
        """Test pulling and running an image from Docker Hub"""
        print("\n" + "="*60)
        print("PULL & RUN IMAGE FROM DOCKER HUB")
        print("="*60)
        
        # Ask user for image name
        print("\n💡 Popular images:")
        print("   - nginx:alpine (lightweight web server)")
        print("   - redis:alpine (in-memory database)")
        print("   - postgres:alpine (SQL database)")
        print("   - mongo:latest (NoSQL database)")
        print("   - node:20-alpine (Node.js runtime)")
        print("   - python:3.11-alpine (Python runtime)")
        
        image_name = input("\nEnter Docker Hub image name: ").strip()
        
        # Use default if empty
        if not image_name:
            image_name = "nginx:alpine"
            print(f"   No input provided, using default: {image_name}")
        
        print(f"\n🐳 Pulling image: {image_name}")
        
        try:
            # Try to pull the image
            try:
                print("📥 Pulling from Docker Hub...")
                image = self.docker_client.images.pull(image_name)
                print(f"✅ Image pulled successfully!")
                print(f"   Tags: {image.tags}")
                print(f"   Size: {round(image.attrs.get('Size', 0) / (1024*1024), 2)} MB")
            except docker.errors.ImageNotFound:
                print(f"❌ Image '{image_name}' not found on Docker Hub")
                print("\n💡 Make sure:")
                print("   1. Image name is correct (format: name:tag or repository/name:tag)")
                print("   2. Image exists on Docker Hub (check: https://hub.docker.com)")
                print("   3. Image is public (or you're logged in with 'docker login')")
                return False
            except Exception as e:
                print(f"❌ Failed to pull image: {e}")
                return False
            
            # Run the container
            print(f"\n🚀 Running container from: {image_name}")
            container = self.docker_client.containers.run(
                image_name,
                detach=True,
                publish_all_ports=True,
                remove=True,
                name=f"docker-hub-{image_name.replace(':', '-').replace('/', '-')}"
            )
            
            print(f"✅ Container running with ID: {container.short_id}")
            
            # Get port mappings
            container.reload()
            port_mappings = container.ports
            
            if port_mappings:
                print(f"\n📡 Port mappings:")
                target_port = None
                for container_port, host_bindings in port_mappings.items():
                    if host_bindings:
                        host_port = host_bindings[0]['HostPort']
                        print(f"   {container_port} -> localhost:{host_port}")
                        if not target_port:
                            target_port = host_port
                
                if target_port:
                    print(f"\n🌐 Opening http://localhost:{target_port} in browser...")
                    time.sleep(2)
                    webbrowser.open(f'http://localhost:{target_port}')
            else:
                print("\n⚠️  No ports exposed. Container is running in background.")
            
            # Wait for user
            print("\n✅ Container is running successfully!")
            input("\n⏸️  Press Enter to stop the container...")
            
            # Stop container
            print("\n🛑 Stopping container...")
            container.stop()
            print("✅ Container stopped and removed")
            return True
            
        except Exception as e:
            print(f"❌ Failed to run container: {e}")
            import traceback
            traceback.print_exc()
            # Cleanup if container exists
            try:
                container = self.docker_client.containers.get(f"docker-hub-{image_name.replace(':', '-').replace('/', '-')}")
                container.stop()
                container.remove()
            except:
                pass
            return False
    
    def test_individual_dockerfile(self):
        """Test building from individual Dockerfile (without compose)"""
        print("\n" + "="*60)
        print("TEST 3: Build from Dockerfile (No Compose)")
        print("="*60)
        
        try:
            # Build context
            build_context = self.test_dir
            dockerfile_name = "Dockerfile"
            image_tag = "test-dockerfile-build:latest"
            
            print(f"🔨 Building image: {image_tag}")
            print(f"   Context: {build_context}")
            print(f"   Dockerfile: {dockerfile_name}")
            
            # Build image
            image, build_logs = self.docker_client.images.build(
                path=build_context,
                dockerfile=dockerfile_name,
                tag=image_tag,
                rm=True
            )
            
            # Print build logs
            for log in build_logs:
                if 'stream' in log:
                    print(f"   {log['stream'].strip()}")
            
            print("✅ Image built successfully!")
            
            # Run container
            print("\n🚀 Running container...")
            container = self.docker_client.containers.run(
                image_tag,
                detach=True,
                publish_all_ports=True,
                remove=True,
                name="test-dockerfile-container"
            )
            
            print(f"✅ Container running with ID: {container.short_id}")
            
            # Get port mappings
            container.reload()
            port_mappings = container.ports
            print(f"\n📡 Port mappings:")
            
            target_port = None
            for container_port, host_bindings in port_mappings.items():
                if host_bindings:
                    host_port = host_bindings[0]['HostPort']
                    print(f"   {container_port} -> localhost:{host_port}")
                    if container_port == '80/tcp':
                        target_port = host_port
            
            if target_port:
                print(f"\n🌐 Opening http://localhost:{target_port}...")
                time.sleep(2)
                webbrowser.open(f'http://localhost:{target_port}')
            
            # Wait for user
            input("\n⏸️  Press Enter to stop the container...")
            
            # Stop container
            container.stop()
            print("✅ Container stopped and removed")
            
            # Remove image
            self.docker_client.images.remove(image_tag, force=True)
            print(f"✅ Image {image_tag} removed")
            
            return True
            
        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def run_all_tests(self):
        """Run Docker Hub image pull and run"""
        print("="*60)
        print("DOCKER HUB IMAGE RUNNER")
        print("="*60)
        
        # Setup
        if not self.setup_docker_client():
            print("\n❌ Cannot proceed without Docker. Please install Docker Desktop.")
            return
        
        # Directly run Docker Hub pull test
        self.test_docker_image_pull()


if __name__ == "__main__":
    tester = DockerTester()
    try:
        tester.run_all_tests()
    except KeyboardInterrupt:
        print("\n\n🛑 Operation cancelled by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n✅ Script completed")
