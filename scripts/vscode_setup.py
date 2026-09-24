"""Point VS Code's C/C++ extension at the headers this checkout builds against."""

import json
import sys
import sysconfig
from pathlib import Path

import nanobind

ROOT = Path(__file__).resolve().parent.parent
PROPERTIES = ROOT / ".vscode" / "c_cpp_properties.json"
CONFIGURATION = "sharedbox"


def include_paths() -> list[str]:
    vcpkg = sorted(p for p in (ROOT / "build" / "vcpkg_installed").glob("*/include") if p.is_dir())
    if not vcpkg:
        raise SystemExit("build/vcpkg_installed has no headers yet; run `uv sync --dev` first")
    nanobind_root = Path(nanobind.include_dir()).parent
    paths = [
        ROOT / "src" / "sharedbox" / "_native",
        Path(sysconfig.get_path("include")),
        Path(nanobind.include_dir()),
        nanobind_root / "ext" / "robin_map" / "include",
        *vcpkg,
    ]
    return [path.as_posix() for path in paths if path.is_dir()]


def defines() -> list[str]:
    names = ["BOOST_ALL_NO_LIB", "WIN32_LEAN_AND_MEAN", "NOMINMAX", "_WIN32_WINNT=0x0A00"] if sys.platform == "win32" else []
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        names.append("Py_GIL_DISABLED=1")
    return names


def main() -> None:
    try:
        properties = json.loads(PROPERTIES.read_text(encoding="utf-8")) if PROPERTIES.exists() else {}
    except json.JSONDecodeError as error:
        raise SystemExit(f"{PROPERTIES} is not plain JSON ({error}); remove comments or the file and run again")
    others = [c for c in properties.get("configurations", []) if c.get("name") != CONFIGURATION]
    configuration = {
        "name": CONFIGURATION,
        "includePath": include_paths(),
        "defines": defines(),
        "cppStandard": "c++17",
    }
    properties["configurations"] = [*others, configuration]
    properties.setdefault("version", 4)
    PROPERTIES.parent.mkdir(exist_ok=True)
    PROPERTIES.write_text(json.dumps(properties, indent=4) + "\n", encoding="utf-8")
    print(f"wrote the '{CONFIGURATION}' configuration to {PROPERTIES}")


if __name__ == "__main__":
    main()
