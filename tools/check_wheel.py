"""Install the built wheel in a clean venv and smoke-test native entry points."""

import os
import shutil
import subprocess
import tempfile
import tomllib
import venv
from pathlib import Path

version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
wheel = Path("dist", f"is_mu_reader-{version}-py3-none-any.whl").resolve()
with tempfile.TemporaryDirectory(prefix="is mu wheel ") as tmp:
    root = Path(tmp)
    venv.create(root / ".venv", with_pip=True)
    scripts = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)
    subprocess.run(
        [str(scripts / ("ismu.exe" if os.name == "nt" else "ismu")), "--help"], cwd=root, check=True
    )
    subprocess.run([str(python), "-m", "ismu", "--help"], cwd=root, check=True)
    subprocess.run(
        [
            str(python),
            "-c",
            "from ismu.transport import Transport; t = Transport('state'); t._save(); Transport('state')",
        ],
        cwd=root,
        check=True,
    )
    launcher = "ismu.cmd" if os.name == "nt" else "ismu"
    shutil.copy2(launcher, root / launcher)
    command = ["cmd", "/c", str(root / launcher)] if os.name == "nt" else [str(root / launcher)]
    subprocess.run([*command, "--help"], cwd=root, check=True)
