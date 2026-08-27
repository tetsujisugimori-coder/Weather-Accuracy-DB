from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
import uuid
import zipfile
from pathlib import Path


class WheelPackagingTests(unittest.TestCase):
    def test_wheel_resources_and_installed_cli_outside_repository(self) -> None:
        repository = Path(__file__).resolve().parent.parent
        workspace = repository.parent / f".weatherdb-wheel-test-{uuid.uuid4().hex}"
        source = workspace / "source"
        distribution = workspace / "dist"
        installation = workspace / "installation"
        runtime = workspace / "runtime"
        temporary = workspace / "temp"
        workspace.mkdir()
        try:
            shutil.copytree(
                repository,
                source,
                ignore=shutil.ignore_patterns(
                    ".git",
                    "data",
                    "__pycache__",
                    "*.pyc",
                    "*.sqlite3*",
                    "build",
                    "dist",
                    "*.egg-info",
                ),
            )
            distribution.mkdir()
            temporary.mkdir()
            offline_environment = os.environ.copy()
            offline_environment["PIP_NO_INDEX"] = "1"
            offline_environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            offline_environment["TEMP"] = str(temporary)
            offline_environment["TMP"] = str(temporary)
            offline_environment["TMPDIR"] = str(temporary)
            offline_environment.pop("PYTHONPATH", None)
            build_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    str(source),
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(distribution),
                ],
                cwd=workspace,
                env=offline_environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                build_result.returncode,
                0,
                msg=f"wheel build failed:\n{build_result.stdout}\n{build_result.stderr}",
            )
            wheels = list(distribution.glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as wheel:
                members = set(wheel.namelist())
            self.assertIn("weatherdb/resources/schema.sql", members)
            self.assertIn("weatherdb/resources/kanagawa.json", members)

            scripts = installation / ("Scripts" if os.name == "nt" else "bin")
            cli = scripts / ("weatherdb.exe" if os.name == "nt" else "weatherdb")
            install_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--prefix",
                    str(installation),
                    str(wheels[0]),
                ],
                cwd=workspace,
                env=offline_environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                install_result.returncode,
                0,
                msg=f"wheel install failed:\n{install_result.stdout}\n{install_result.stderr}",
            )

            if os.name == "nt":
                site_packages = installation / "Lib" / "site-packages"
            else:
                version = f"python{sys.version_info.major}.{sys.version_info.minor}"
                site_packages = installation / "lib" / version / "site-packages"
            offline_environment["PYTHONPATH"] = str(site_packages)

            runtime.mkdir()
            database = runtime / "installed.sqlite3"
            outputs = []
            for command in ("init", "import-areas", "status"):
                result = subprocess.run(
                    [str(cli), "--db", str(database), command],
                    check=True,
                    cwd=runtime,
                    env=offline_environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                outputs.append(result.stdout)
            self.assertIn("地域 45件 / 観測地点 11件", outputs[1])
            self.assertIn("登録地域数: 45", outputs[2])
            self.assertIn("観測地点数: 11", outputs[2])
        finally:
            if workspace.exists():
                shutil.rmtree(workspace)


if __name__ == "__main__":
    unittest.main()
