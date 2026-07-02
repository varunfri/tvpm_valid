import subprocess
import sys
import time
import os
import signal
from dotenv import load_dotenv

# Load env variables
load_dotenv()

def kill_process_using_port(port):
    """Checks if a port is in use and kills the occupying process (Windows/macOS/Linux)."""
    import subprocess
    import sys
    import os
    
    if sys.platform == "win32":
        try:
            # Run netstat to find the PID of the process using the port on Windows
            cmd = f"netstat -ano | findstr :{port}"
            output = subprocess.check_output(cmd, shell=True, text=True)
            for line in output.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit() and int(pid) > 0:
                    print(f"Found orphaned process {pid} using port {port}. Terminating...")
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            pass
        except Exception as e:
            print(f"Error freeing port {port}: {e}")
    else:  # macOS & Linux
        try:
            # lsof -t -i :port returns only the PIDs using the port
            cmd = f"lsof -t -i :{port}"
            pids = subprocess.check_output(cmd, shell=True, text=True).strip().split('\n')
            for pid in pids:
                pid = pid.strip()
                if pid and pid.isdigit():
                    print(f"Found orphaned process {pid} using port {port}. Terminating...")
                    os.kill(int(pid), signal.SIGKILL)
        except subprocess.CalledProcessError:
            # lsof returns non-zero code if no process is found on the port
            pass
        except Exception as e:
            print(f"Error freeing port {port}: {e}")


def run_services():
    print("Starting TVPM Validation Services...")
    
    # Get ports from environment variables or use default values
    backend_port = os.getenv("BACKEND_PORT", "8050")
    frontend_port = os.getenv("FRONTEND_PORT", "8501")
    
    # Automatically clean up orphaned processes on those ports before launching
    kill_process_using_port(backend_port)
    kill_process_using_port(frontend_port)
    
    # Verify environment
    backend_script = "backend.main:app"
    frontend_path = os.path.join("frontend", "app.py")
    
    # Build commands
    backend_cmd = [sys.executable, "-m", "uvicorn", backend_script, "--host", "127.0.0.1", "--port", backend_port]
    frontend_cmd = [sys.executable, "-m", "streamlit", "run", frontend_path, "--server.port", frontend_port]
    
    processes = []
    
    try:
        # Start backend
        print(f"Launching FastAPI Backend on http://127.0.0.1:{backend_port} ...")
        backend_proc = subprocess.Popen(backend_cmd)
        processes.append(backend_proc)
        
        # Give backend a moment to start
        time.sleep(2)
        
        # Start frontend
        print(f"Launching Streamlit Frontend on http://127.0.0.1:{frontend_port} ...")
        frontend_proc = subprocess.Popen(frontend_cmd)
        processes.append(frontend_proc)
        
        print("\nBoth services started! Press Ctrl+C to stop services.\n")
        
        # Simple loop to wait
        services = [("Backend (FastAPI)", backend_proc), ("Frontend (Streamlit)", frontend_proc)]
        while True:
            # Check if either process terminated
            for name, p in services:
                if p.poll() is not None:
                    print(f"\n[ERROR] {name} terminated with exit code {p.returncode}")
                    raise KeyboardInterrupt
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nStopping services...")
        for p in processes:
            if p.poll() is None:
                print(f"Terminating process {p.pid}...")
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    print(f"Killing process {p.pid}...")
                    p.kill()
        print("Services stopped.")

if __name__ == "__main__":
    run_services()
