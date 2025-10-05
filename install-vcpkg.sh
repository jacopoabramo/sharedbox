#!/bin/bash
if [ "$EUID" -ne 0 ]; then 
    echo "Error: This script requires sudo privileges to install packages and create symlinks"
    echo "Usage: sudo bash install-vcpkg.sh"
    exit 1
fi

# Check if vcpkg is already installed
if [ -d "/opt/vcpkg" ]; then
    echo "vcpkg is already installed at /opt/vcpkg"
    
    # Check if vcpkg executable exists
    if [ -f "/opt/vcpkg/vcpkg" ]; then
        echo "vcpkg executable found"
    else
        echo "Warning: /opt/vcpkg exists but vcpkg executable not found"
        echo "You may need to run: sudo /opt/vcpkg/bootstrap-vcpkg.sh"
    fi
else
    echo "Installing vcpkg..."
    
    sudo apt update
    sudo apt install -y curl zip unzip tar git
    
    cd /opt && git clone https://github.com/microsoft/vcpkg.git
    
    sudo /opt/vcpkg/bootstrap-vcpkg.sh
    
    echo "vcpkg installed successfully at /opt/vcpkg"
fi

# Check if symlink exists
if [ -L "/usr/local/bin/vcpkg" ]; then
    echo "Symlink /usr/local/bin/vcpkg already exists"
elif [ -f "/opt/vcpkg/vcpkg" ]; then
    echo "Creating symlink..."
    sudo ln -s /opt/vcpkg/vcpkg /usr/local/bin/vcpkg
    echo "Symlink created at /usr/local/bin/vcpkg"
fi

# Check VCPKG_ROOT environment variable
echo ""
echo "Checking VCPKG_ROOT environment variable..."
if [ -z "$VCPKG_ROOT" ]; then
    echo "VCPKG_ROOT is not set"
    echo "Set it to /opt/vcpkg for proper functionality"
elif [ "$VCPKG_ROOT" = "/opt/vcpkg" ]; then
    echo "VCPKG_ROOT is correctly set to: $VCPKG_ROOT"
else
    echo "Warning: VCPKG_ROOT is set to: $VCPKG_ROOT"
    echo "Expected: /opt/vcpkg"
fi

echo ""
echo "Installation check complete!"
echo "vcpkg version:"
vcpkg version 2>/dev/null || echo "vcpkg command not found in PATH"