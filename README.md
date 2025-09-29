# `sharedbox`

> [!WARNING]
> This project is a work in progress; be patient or feel free to contribute.

Python inter-process shared containers leveraging `boost::interprocess`.

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
- `cython>=3.1`
- A C++17 compatible compiler

First [install and boostrap vcpkg](https://learn.microsoft.com/en-us/vcpkg/get_started/get-started-vscode?pivots=shell-cmd) somewhere in your system.

```sh
# for Windows it is reccomended to install it in C:\
cd C:\

git clone https://github.com/microsoft/vcpkg.git
cd vcpkg && bootstrap-vcpkg.bat
```

Don't forget to add it to your `PATH`.

Also, add an environment variable pointing to the root of `vcpkg`:

```sh
set "VCPKG_ROOT=C:\path\to\vcpkg"
```

Clone this repository, then install `boost::interprocess`:

```
vcpkg install boost-interprocess
```

Finally, through `uv`, install the package in a virtual environment:

```sh
uv venv --python 3.10
uv pip install -e .[dev]
```

## License

Licensed under [Apache 2.0](./LICENSE)
