import subprocess
import sys
import os

def run_command(command):
    """Runs a command and prints its output."""
    print(f"Running command: {' '.join(command)}")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    for line in process.stdout:
        print(line, end='')
    process.wait()
    if process.returncode != 0:
        print(f"Error executing command: {' '.join(command)}")
        sys.exit(process.returncode)

def main():
    # Uninstall the existing package
    run_command([sys.executable, "-m", "pip", "uninstall", "-y", "openpylivox"])

    # Install from local source
    openpylivox_dir = os.path.join(os.path.dirname(__file__), "openpylivox-master")
    run_command([sys.executable, "-m", "pip", "install", openpylivox_dir])

    # Run the main application
    run_command([sys.executable, "run.py"])

if __name__ == "__main__":
    main()