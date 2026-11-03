"""Deploy the judge demo separately from the physical-device gateway."""
from pathlib import Path
import subprocess
import sys

import modal

REMOTE = "/opt/confio"
app = modal.App("confio-demo")
image = None
if modal.is_local():
    ROOT = Path(__file__).resolve().parents[2]
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install_from_requirements(str(ROOT / "deploy/requirements.txt"))
        .env({"CONFIO_DEMO_PUBLIC": "1"})
        .add_local_file(str(ROOT / "web/demo/server.py"), REMOTE + "/web/demo/server.py")
        .add_local_file(str(ROOT / "web/demo/index.html"), REMOTE + "/web/demo/index.html")
        .add_local_dir(str(ROOT / "web/demo/assets"), REMOTE + "/web/demo/assets")
        .add_local_file(str(ROOT / "web/call/styles.css"), REMOTE + "/web/call/styles.css")
        .add_local_file(str(ROOT / "media/camera-test-2.jpg"), REMOTE + "/media/camera-test-2.jpg")
        .add_local_file(str(ROOT / "scripts/voice_agent.py"), REMOTE + "/scripts/voice_agent.py")
        .add_local_file(str(ROOT / "scripts/photo_tools.py"), REMOTE + "/scripts/photo_tools.py")
        .add_local_file(str(ROOT / "esp32/voice/voice_link.py"), REMOTE + "/esp32/voice/voice_link.py")
        .add_local_file(str(ROOT / "prompts/assistant_system.md"), REMOTE + "/prompts/assistant_system.md")
    )


@app.function(image=image, secrets=[modal.Secret.from_name("confio-demo")],
              cpu=0.5, memory=512, max_containers=1, scaledown_window=300)
@modal.concurrent(max_inputs=16)
@modal.web_server(8080, label="confio-judge-demo", startup_timeout=60)
def serve():
    subprocess.Popen([sys.executable, REMOTE + "/web/demo/server.py", "--public"])
