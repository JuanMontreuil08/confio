"""Deploy with: python -m modal deploy deploy/modal_app.py"""
from pathlib import Path
import subprocess
import sys

import modal

ROOT = Path(__file__).resolve().parents[1]
app = modal.App("confio")
# Explicit allowlist: never upload the repo root, .env, contacts or local recordings.
image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install_from_requirements(str(ROOT / "deploy" / "requirements.txt"))
         .add_local_dir(str(ROOT / "scripts"), "/opt/confio/scripts", ignore=["tests/", "__pycache__/", "*.pyc"])
         .add_local_file(str(ROOT / "esp32" / "voice" / "voice_link.py"), "/opt/confio/esp32/voice/voice_link.py")
         .add_local_dir(str(ROOT / "prompts"), "/opt/confio/prompts"))


@app.function(image=image, secrets=[modal.Secret.from_name("confio")],
              cpu=0.5, memory=512, min_containers=0, max_containers=1,
              scaledown_window=60, timeout=86400)
@modal.concurrent(max_inputs=16)
@modal.web_server(8000, startup_timeout=60)
def gateway():
    # web_server requires a detached subprocess. It stays alive with the container.
    subprocess.Popen([sys.executable, "/opt/confio/scripts/cloud_agent.py"])
