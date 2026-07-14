# Deploy Robin on a fresh Mac

## Supported host

- Dedicated Apple-silicon Mac; macOS 14 or newer.
- Persistent, locally logged-in graphical session. SSH alone does not create the WindowServer session Robin needs.
- One dedicated macOS user and one dedicated, already-authorized Zoom account.
- Fixed 1920×1080 workspace where available; no unrelated personal applications or documents.

## Bootstrap

Clone the public repository as the dedicated user, then run:

```bash
./scripts/bootstrap-macos.sh
./scripts/keychain-secret.sh set OPENAI_API_KEY
./scripts/keychain-secret.sh set ROBIN_PANEL_TOKEN
./scripts/install-launchd.sh
```

The bootstrap installs pinned dependency classes from `Brewfile`, including Zoom and two independent BlackHole devices. The native helper creates stable aggregates:

- `Robin Speaker` wraps BlackHole 2ch and receives Zoom’s meeting output for Realtime input.
- `Robin Microphone` wraps BlackHole 16ch and receives Realtime output for Zoom microphone input.

Independent devices prevent Robin’s speech from looping directly back into its meeting-input capture.

## One-time protected-console work

1. Sign into the dedicated Zoom user in Zoom Workplace. Do not store its password in Robin.
2. In Zoom Audio settings, select `Robin Microphone` as microphone and `Robin Speaker` as speaker. Disable automatic device switching.
3. Run `apps/mac-helper/.build/release/RobinMacHelper --socket /tmp/robin-helper.sock` once from Terminal.
4. In System Settings → Privacy & Security, grant the signed `RobinMacHelper` Screen & System Audio Recording, Accessibility, Input Monitoring, and Microphone permissions.
5. Restart the helper through `./scripts/install-launchd.sh`. Permission grants bind to the signed binary identity; sign with a stable Developer ID in production by setting `ROBIN_CODESIGN_IDENTITY` before bootstrap.
6. Keep Zoom signed in, quit it, and run `./scripts/doctor.sh`.

The control panel binds to `127.0.0.1` by default and requires a Keychain-backed bearer token. Access it only over a private authenticated tunnel or private-network reverse proxy; never bind it publicly.

## Verification

Run the acceptance sequence in `docs/E2E.md` three consecutive times. Save the redacted trace filenames and doctor output in the release record. A change to macOS, Zoom, BlackHole, the helper signature, display resolution, or model configuration invalidates the three-run record.
