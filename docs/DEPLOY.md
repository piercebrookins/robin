# Deploy Robin on a fresh Mac

## Supported host

- Dedicated Apple-silicon Mac; macOS 14 or newer.
- Persistent, locally logged-in graphical session. SSH alone does not create the WindowServer session Robin needs.
- One dedicated macOS user and one dedicated, already-authorized Zoom account.
- Fixed workspace resolution (1920×1080 where available); no unrelated personal applications or documents. Set `ROBIN_EXPECTED_CAPTURE` to the ScreenCaptureKit pixel dimensions recorded for the release host so `doctor` detects drift; these can differ from the panel's physical Retina resolution.

The supported and last-verified dependency baselines are machine-readable in `infra/versions.env`. GitHub CI continuously verifies macOS 14, Node 24, and Swift tools 5.10; the development host and Zoom/BlackHole versions are recorded separately in that file.

## Bootstrap

Clone the public repository as the dedicated user, then run:

```bash
./scripts/bootstrap-macos.sh
./scripts/keychain-secret.sh set OPENAI_API_KEY
./scripts/keychain-secret.sh generate ROBIN_PANEL_TOKEN
./scripts/install-launchd.sh
```

The first bootstrap run may install BlackHole and stop with a reboot instruction. Reboot and rerun the same command; it is idempotent. Bootstrap prepares the build and audio routes but deliberately does not install persistent services before secrets, permissions, and Zoom are ready.

The bootstrap installs dependency classes from `Brewfile`, including Zoom and two independent BlackHole devices. The native helper creates stable aggregates:

- `Robin Speaker` wraps BlackHole 2ch and receives Zoom’s meeting output for Realtime input.
- `Robin Microphone` wraps BlackHole 16ch and receives Realtime output for Zoom microphone input.

Independent devices prevent Robin’s speech from looping directly back into its meeting-input capture.

## One-time protected-console work

1. Sign into the dedicated Zoom user in Zoom Workplace. Do not store its password in Robin.
2. In Zoom Audio settings, select `Robin Microphone` as microphone and `Robin Speaker` as speaker. Disable automatic device switching.
3. Run `apps/mac-helper/.build/release/RobinMacHelper --socket /tmp/robin-helper.sock` once from Terminal and leave it running while permissions are granted.
4. In System Settings → Privacy & Security, grant the signed `RobinMacHelper` Screen & System Audio Recording, Accessibility, Input Monitoring, and Microphone permissions.
5. Stop the foreground helper with Control-C, then run `./scripts/install-launchd.sh`. The installer refuses to start until the signed helper, both Keychain entries, and both stable audio routes exist. Permission grants bind to the signed binary identity; sign with a stable Developer ID in production by setting `ROBIN_CODESIGN_IDENTITY` before bootstrap.
6. Keep Zoom signed in, quit it, and run `./scripts/doctor.sh`.
7. For the first control-panel sign-in, run `./scripts/keychain-secret.sh copy ROBIN_PANEL_TOKEN` from the protected console and paste it into the prompt. The clipboard clears automatically after 60 seconds.

The control panel binds to `127.0.0.1` by default and requires a Keychain-backed bearer token. Access it only over a private authenticated tunnel or private-network reverse proxy; never bind it publicly.

## Verification

Run the acceptance sequence in `docs/E2E.md` three consecutive times. Save the redacted trace filenames and doctor output in the release record. A change to macOS, Zoom, BlackHole, the helper signature, display resolution, or model configuration invalidates the three-run record.
