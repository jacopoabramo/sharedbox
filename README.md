# `sharedbox`

> [!WARNING]
> This project is a work in progress; be patient or feel free to contribute.

Python inter-process shared containers leveraging the [`boost::interprocess`](https://www.boost.org/doc/libs/latest/doc/html/interprocess.html) library.

## Installation

It is reccomended to install `sharedbox` in a virtual environment; for example using `uv`:

```sh
uv venv --python 3.10
.venv\Scripts\activate
uv pip install sharedbox
```

## Quick Start

```python
import multiprocessing as mp
from sharedbox import SharedDict

# Use in child processes
def worker(segment_name):
    d = SharedDict(segment_name, create=False)  # Connect to existing
    d["worker_data"] = "Hello from worker!"
    d.close()  # Close in child process

if __name__ == "__main__":
    # Create a shared dictionary
    d = SharedDict("my_segment", create=True, size=10*1024*1024)
    d["hello"] = "world"
    d["data"] = [1, 2, 3, 4, 5]

    # Start worker
    p = mp.Process(target=worker, args=("my_segment",))
    p.start()
    p.join()

    print(d["worker_data"])  # "Hello from worker!"
    d.close() # Close in the main process
    d.unlink()  # Unlink (free resources)
```

## Initialization with Data

You can initialize SharedDict with existing data for convenient setup:

```python
import numpy as np
from sharedbox import SharedDict

# Initialize with mixed data types
config_data = {
    "app_name": "MyApp",
    "version": "1.0",
    "max_users": 1000,
    "features": ["auth", "logging"],
    "model_weights": np.array([0.1, 0.3, 0.6])
}

# Create SharedDict with initial data
shared_config = SharedDict("config", config_data, create=True)

# Data is immediately available
print(shared_config["app_name"])  # "MyApp"
print(shared_config["model_weights"])  # numpy array

# when a child process doesn't need the memory anymore call "close"
shared_config.close()

# the main process is in charge of cleaning up; call "unlink" to do so,
# similarly to a regular python SharedMemory object;
# make sure that the main process calls "close" before as well
shared_config.unlink()
```

## Limitations

- Nested dictionaries are "currently" unsupported
- Project is quite unstable, might not provide great performance boost at this time
- macOS unsupported

## Examples

The `examples/` folder contains some code examples on how to use the package.

## Building locally

### Requirements

- [`git`](https://git-scm.com/downloads)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Python >= 3.10
- [`vcpkg`](https://vcpkg.io/en/)
- [`CMake`](https://cmake.org/download/) >= 3.15
- A C++17 compatible compiler (MSVC on Windows, GCC on Linux)

> [!NOTE]
> The build system automatically detects `vcpkg` and installed libraries through the `VCPKG_ROOT` environment variable.
> Make sure it's set before building.

### Install and configure vcpkg

First, [install and bootstrap vcpkg](https://learn.microsoft.com/en-us/vcpkg/get_started/get-started?pivots=shell-cmd) somewhere in your system.

#### Windows
```cmd
# It is recommended to install in C:\
cd C:\
git clone https://github.com/microsoft/vcpkg.git
cd vcpkg
bootstrap-vcpkg.bat

# Set the VCPKG_ROOT environment variable (permanently)
setx VCPKG_ROOT "C:\vcpkg"

# Add vcpkg to your PATH (permanently)
setx PATH "%PATH%;C:\vcpkg"
```

#### Linux
You can use the `install-vcpkg.sh` script which will
automatically install `vcpkg` in the `/opt` folder
and set the `VCPKG_ROOT` environment variable.
The script must be run with super user priviledges:

```bash
sudo bash install-vcpkg.sh
```

### Install `boost-interprocess`

```bash
# From anywhere (vcpkg should be in PATH)
vcpkg install boost-interprocess
```

### Build the package

Clone this repository and install using `uv`:

```bash
# Clone the repository
git clone https://github.com/jacopoabramo/sharedbox.git
cd sharedbox

# Create virtual environment and install in development mode
uv venv --python 3.10
uv pip install -e .[dev]
```

### Running tests

You can run tests using [nox](https://nox.thea.codes/en/stable/index.html)

```bash
# install nox as a tool
uv tool install nox
nox -s tests
```

## License

Licensed under [Apache 2.0](./LICENSE)

`sharedbox` is built using the Boost C++ library, which is licensed under the [Boost Software License](https://boost.org.cpp.al/LICENSE_1_0.txt).
