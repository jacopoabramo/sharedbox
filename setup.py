"""
Setup script for sharedbox - A shared memory dictionary using Boost.Interprocess
"""

import os
import platform

from Cython.Build import cythonize
from setuptools import Extension, setup

system = platform.system().lower()
compile_args: list[str] = []
link_args: list[str] = []
libraries: list[str] = []

if system == "windows":
    vcpkg_root = os.getenv("VCPKG_ROOT", "C:/vcpkg")
    includes = [os.path.join(vcpkg_root, "installed", "x64-windows", "include")]
    libs = [
        os.path.join(vcpkg_root, "installed", "x64-windows", "lib"),
        os.path.join(vcpkg_root, "installed", "x64-windows", "bin"),
    ]
    compile_args.extend(
        [
            "/std:c++17",
            "/EHsc",  # Exception handling model
            "/DBOOST_ALL_NO_LIB",  # Disable Boost auto-linking (we manage dependencies)
            "/D_WIN32_WINNT=0x0601",  # Target Windows 7+ (required for Boost.Interprocess)
            "/DWIN32_LEAN_AND_MEAN",  # Reduce Windows header overhead
            "/DNOMINMAX",  # Prevent min/max macro conflicts
            "/O2",  # Optimization
        ]
    )
    libraries.extend(
        [
            "kernel32",  # Core Windows APIs
            "user32",  # User interface APIs
            "advapi32",  # Advanced Windows APIs (security, registry)
        ]
    )

elif system == "linux":
    includes = ["/usr/include", "/usr/include/boost"]
    libs = ["/usr/lib"]
    compile_args.extend(
        [
            "-std=c++17",  # C++17 standard
            "-O3",  # High optimization level
            "-DBOOST_ALL_NO_LIB",  # Disable Boost auto-linking
            "-D_GNU_SOURCE",  # Enable GNU extensions
        ]
    )
    libraries.extend(
        [
            "rt",  # Real-time extensions (for shm_open, etc.)
            "pthread",  # POSIX threads
        ]
    )
else:
    raise RuntimeError(f"Unsupported platform: {system}")

includes.append("cpp_src")  # Project-specific headers

extensions = [
    Extension(
        name="sharedbox._shareddict",
        sources=[
            "src/sharedbox/_shareddict.pyx",
            "cpp_src/shared_dict.cpp",
        ],
        include_dirs=includes,
        libraries=libraries,
        library_dirs=libs,
        language="c++",
        extra_compile_args=compile_args,
        extra_link_args=link_args,
    ),
    Extension(
        name="sharedbox.utils",
        sources=[
            "src/sharedbox/utils.py",
        ],
        language="c++",
        extra_compile_args=compile_args,
    ),
]

ext_modules = cythonize(
    extensions,
    compiler_directives={
        "embedsignature": True,  # Include function signatures in docstrings
        "boundscheck": False,  # Disable bounds checking for performance
        "wraparound": False,  # Disable negative index wrapping
        "language_level": 3,  # Use Python 3
        "c_string_type": "unicode",  # Handle strings as unicode
        "c_string_encoding": "utf8",  # Use UTF-8 encoding
    },
    build_dir="build/cython",  # Directory for generated C++ files
    annotate=True,  # Generate HTML annotation files for debugging
)

# Setup call - project metadata comes from pyproject.toml
setup(
    ext_modules=ext_modules,
    zip_safe=False,  # Extensions cannot be run from zip files
)
