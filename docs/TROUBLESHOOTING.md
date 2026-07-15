# Troubleshooting

Start with `./scripts/doctor.sh`. It reports presence and status but never secret values.

## Helper unavailable

Run `launchctl print gui/$(id -u)/com.robin.helper` and inspect `~/Library/Logs/Robin/helper.error.log`. If the helper was rebuilt with a different signature, grant macOS permissions to the newly signed binary and restart the service.

## Screen capture is blank or denied

Grant Screen & System Audio Recording to the signed helper in Privacy & Security, then fully restart the helper. A permission toggle does not update an already-running process.

## Accessibility actions fail

Grant Accessibility and Input Monitoring. Robin first searches the accessibility tree; if a Zoom update changes labels, it falls back to current-screen coordinates from GPT computer use. Three repeated failures trigger takeover.

## Audio is silent or echoes

Confirm both `Robin Speaker` and `Robin Microphone` exist and that Zoom uses the matching speaker/microphone selections. They must wrap different BlackHole devices. Rerun `RobinMacHelper configure-audio`, restart Zoom, and run doctor. Do not enable a monitor that routes Robin Microphone back into Robin Speaker.

## Realtime disconnects

The daemon reports the failure and the audio bridge bounds its queued output. Confirm network connectivity and Keychain presence, then restart `com.robin.agent`. Repeated model failures enter takeover.

## Zoom expired its login

Robin does not enter credentials. Take over through the protected console, sign into the dedicated Zoom account, quit Zoom, and return control to Robin.

## Control panel is unauthorized

Generate a new token with `./scripts/keychain-secret.sh generate ROBIN_PANEL_TOKEN`, reinstall/restart the launch service, then use `./scripts/keychain-secret.sh copy ROBIN_PANEL_TOKEN` and paste it into the panel prompt. The protected-console clipboard clears after 60 seconds. Do not expose the panel on a public interface or print the token in a terminal on Robin’s workspace display.
