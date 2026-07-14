#!/bin/zsh
set -euo pipefail
defaults write NSGlobalDomain NSAutomaticWindowAnimationsEnabled -bool false
defaults write NSGlobalDomain NSWindowResizeTime -float 0.001
defaults write com.apple.dock autohide-delay -float 0
defaults write com.apple.dock expose-animation-duration -float 0.1
defaults write com.apple.notificationcenterui doNotDisturb -bool true || true
defaults -currentHost write com.apple.screensaver idleTime -int 0
sudo pmset -a sleep 0 displaysleep 0 disksleep 0
killall Dock 2>/dev/null || true
print "Desktop animations, sleep, and notification interruptions are minimized. Set display resolution to 1920×1080 in System Settings if the host supports it."
