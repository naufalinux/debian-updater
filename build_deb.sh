#!/bin/bash
# Convenience script to build the Debian package using dpkg-deb.
# This does not require debhelper / rules, just standard dpkg tools.

set -e

# Change directory to the script's directory (the workspace root)
cd "$(dirname "$0")"

# Define version and package name
PACKAGE_NAME="debian-updater"
VERSION="1.0"
OUTPUT_DEB="${PACKAGE_NAME}_${VERSION}_all.deb"
STAGING_DIR="build_staging"

echo "Building Debian package: $OUTPUT_DEB..."

# Clean up old build files
rm -rf "$STAGING_DIR"
rm -f "$OUTPUT_DEB"

# Recreate folder structure
mkdir -p "$STAGING_DIR/usr/bin"
mkdir -p "$STAGING_DIR/usr/share/applications"
mkdir -p "$STAGING_DIR/usr/share/doc/$PACKAGE_NAME"
mkdir -p "$STAGING_DIR/DEBIAN"

# Copy python executable and remove .py extension
cp debian_updater.py "$STAGING_DIR/usr/bin/debian-updater"
chmod 755 "$STAGING_DIR/usr/bin/debian-updater"

# Copy desktop file
cp debian/debian-updater.desktop "$STAGING_DIR/usr/share/applications/debian-updater.desktop"

# Copy copyright and changelog (and compress changelog)
cp debian/copyright "$STAGING_DIR/usr/share/doc/$PACKAGE_NAME/copyright"
cp debian/changelog "$STAGING_DIR/usr/share/doc/$PACKAGE_NAME/changelog"
gzip -9n "$STAGING_DIR/usr/share/doc/$PACKAGE_NAME/changelog"

# Write the control file for dpkg-deb
cat << EOF > "$STAGING_DIR/DEBIAN/control"
Package: $PACKAGE_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: Debian Updater Developer <developer@debian.org>
Depends: python3, python3-pyside6.qtwidgets, pkexec, ksshaskpass
Recommends: flatpak
Description: Clean-safe APT and Flatpak updater for Debian 13 trixie
 A simple Qt desktop updater for Debian 13 trixie and KDE Plasma 6.
 The app runs a conservative APT and Flatpak update workflow, writes a
 timestamped log file, and lets you switch between a graphical progress
 view and a terminal-style output view.
EOF

# Build the package
dpkg-deb --root-owner-group --build "$STAGING_DIR" "$OUTPUT_DEB"

# Clean up staging directory
rm -rf "$STAGING_DIR"

echo "Build complete: $OUTPUT_DEB"
