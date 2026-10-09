"""
Builds the two deployment artifacts - the code bundle and the dependency layer - WITHOUT Docker and from any operating
system: the layer is assembled from pre-built Linux wheels (pip's --platform flags), so a Windows or Mac laptop produces
exactly what Lambda needs.   python package.py [output-dir]
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAYER_REQUIREMENTS = ["psycopg[binary]>=3.2,<4", "snowflake-connector-python>=3.12"]
PLATFORMS = ["manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64"]
PYTHON_VERSION = "3.12"
RUNTIME_PROVIDED = {"boto3", "botocore", "s3transfer", "jmespath"}      # already in the Lambda runtime; the connector would otherwise add ~35 MB
LAMBDA_UNZIPPED_LIMIT_MB = 250                                            # code + all layers, uncompressed


def _zip_dir(src: Path, dest: Path, root: str = "") -> int:
    """Zips src (keeping `root` as the top folder) and returns the uncompressed size in bytes."""
    total = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                z.write(f, Path(root) / f.relative_to(src))
                total += f.stat().st_size
    return total


def build_layer(out_dir: Path) -> tuple[Path, int]:
    work = out_dir / "layer_build"
    shutil.rmtree(work, ignore_errors=True)
    target = work / "python"
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", str(target), "--only-binary=:all:",
           "--implementation", "cp", "--python-version", PYTHON_VERSION]
    for p in PLATFORMS:
        cmd += ["--platform", p]
    subprocess.run(cmd + LAYER_REQUIREMENTS, check=True)
    for name in RUNTIME_PROVIDED:
        shutil.rmtree(target / name, ignore_errors=True)
        for d in target.glob(f"{name}-*.dist-info"):
            shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(target / "bin", ignore_errors=True)
    dest = out_dir / "dependencies-layer.zip"
    size = _zip_dir(work, dest)
    return dest, size


def build_code(out_dir: Path) -> tuple[Path, int]:
    work = out_dir / "code_build"
    shutil.rmtree(work, ignore_errors=True)
    for pkg in ("pipeline", "lambdas"):
        shutil.copytree(HERE / pkg, work / pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    dest = out_dir / "pipeline-code.zip"
    return dest, _zip_dir(work, dest)


def main(out: str = "build") -> None:
    out_dir = Path(out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    code, code_size = build_code(out_dir)
    layer, layer_size = build_layer(out_dir)
    total_mb = (code_size + layer_size) / 1e6
    print(f"code  {code.name}: {code.stat().st_size / 1e6:6.1f} MB zipped, {code_size / 1e6:6.1f} MB unzipped")
    print(f"layer {layer.name}: {layer.stat().st_size / 1e6:6.1f} MB zipped, {layer_size / 1e6:6.1f} MB unzipped")
    print(f"total unzipped {total_mb:.0f} MB of the {LAMBDA_UNZIPPED_LIMIT_MB} MB Lambda limit")
    if total_mb >= LAMBDA_UNZIPPED_LIMIT_MB:
        raise SystemExit("The bundle is over Lambda's size limit")


if __name__ == "__main__":
    main(*sys.argv[1:2])
