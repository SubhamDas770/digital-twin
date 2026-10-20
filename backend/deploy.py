import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
PACKAGE_FILES = ("server.py", "lambda_handler.py", "context.py", "resources.py")
OUTPUT_ZIP = BACKEND_DIR / "lambda-deployment.zip"


def install_dependencies(package_dir: Path) -> None:
    if importlib.util.find_spec("pip") is not None:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(package_dir),
            "--requirement",
            str(BACKEND_DIR / "requirements.txt"),
            "--platform",
            "manylinux2014_x86_64",
            "--implementation",
            "cp",
            "--python-version",
            "3.12",
            "--abi",
            "cp312",
            "--only-binary=:all:",
            "--upgrade",
        ]
    else:
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError(
                "pip is unavailable for this Python interpreter. Install pip or uv, "
                "then rerun the deployment build."
            )
        command = [
            uv,
            "pip",
            "install",
            "--target",
            str(package_dir),
            "--requirement",
            str(BACKEND_DIR / "requirements.txt"),
            "--python-version",
            "3.12",
            "--python-platform",
            "x86_64-unknown-linux-gnu",
            "--only-binary",
            ":all:",
            "--upgrade",
        ]

    subprocess.run(command, cwd=BACKEND_DIR, check=True)


def main() -> None:
    print("Creating Lambda deployment package...")

    required_files = [BACKEND_DIR / file for file in PACKAGE_FILES]
    required_files.extend(
        [
            BACKEND_DIR / "summary.txt",
            BACKEND_DIR / "requirements.txt",
            BACKEND_DIR / "data" / "facts.json",
            BACKEND_DIR / "data" / "style.txt",
            BACKEND_DIR / "data" / "linkedin.pdf",
        ]
    )
    missing_files = [path.name for path in required_files if not path.is_file()]
    if missing_files:
        raise FileNotFoundError(
            "Cannot build Lambda package; required files are missing: "
            + ", ".join(missing_files)
        )

    with tempfile.TemporaryDirectory(prefix=".lambda-build-", dir=BACKEND_DIR) as temp_dir:
        temp_path = Path(temp_dir)
        package_dir = temp_path / "package"
        package_dir.mkdir()

        print("Installing Python 3.12 Linux dependencies without Docker...")
        install_dependencies(package_dir)

        print("Copying application code and profile data...")
        for file in PACKAGE_FILES:
            shutil.copy2(BACKEND_DIR / file, package_dir / file)
        shutil.copy2(BACKEND_DIR / "summary.txt", package_dir / "summary.txt")
        shutil.copytree(BACKEND_DIR / "data", package_dir / "data")

        temp_zip = temp_path / "lambda-deployment.zip"
        print("Creating deployment zip...")
        with zipfile.ZipFile(
            temp_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for root, dirs, files in os.walk(package_dir):
                dirs[:] = [directory for directory in dirs if directory != "__pycache__"]
                for file in files:
                    if file.endswith(".pyc"):
                        continue
                    file_path = Path(root) / file
                    archive.write(file_path, file_path.relative_to(package_dir))

        os.replace(temp_zip, OUTPUT_ZIP)

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"Created {OUTPUT_ZIP.name} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
