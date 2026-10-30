"""Every file the code reads at run time ships in the installed package. The image installs the backend with
`pip install` from a context without .git, so a non-Python file is installed only when [tool.setuptools.package-data]
names it; on 2026-10-30 the Claude prompts were left out and every Claude job would have failed in the image while
the tests (run from the source tree) passed."""
import fnmatch
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "ghostline"
# Reference documents copied with their licences (modules/vendor): read by people, never by the code.
NOT_RUNTIME = ("modules/vendor/",)


def _declared() -> dict[str, list[str]]:
    with open(BACKEND / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["tool"]["setuptools"].get("package-data", {})


def test_every_non_python_file_the_package_reads_is_declared_package_data():
    declared = _declared()
    missing = []
    for f in PACKAGE.rglob("*"):
        rel = f.relative_to(PACKAGE).as_posix()
        if f.is_dir() or f.suffix in (".py", ".pyc") or "__pycache__" in rel or rel.startswith(NOT_RUNTIME):
            continue
        pkg = "ghostline." + ".".join(f.relative_to(PACKAGE).parent.parts) if f.parent != PACKAGE else "ghostline"
        patterns = declared.get(pkg, [])
        if not any(fnmatch.fnmatch(f.name, p) for p in patterns):
            missing.append(f"{rel} (package {pkg})")
    assert missing == [], f"not in [tool.setuptools.package-data]: {missing}"


def test_the_prompts_are_declared():
    assert "*.md" in _declared()["ghostline.claude.prompts"]
