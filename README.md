# Robin

Robin is a Mac-hosted agentic coworker that joins ordinary Zoom meetings through the signed-in Zoom Workplace app. It uses OpenAI Realtime for interruptible speech and GPT-5.6 computer use to operate the whole dedicated Mac. There is no Zoom SDK and no private Zoom API.

The repository contains a TypeScript daemon and private control panel, a native Swift ScreenCaptureKit/Accessibility/Core Audio helper, safety and approval policy, redacted audit traces, persistent launch services, clean-machine setup, and a deterministic fake-Zoom simulator.

## Try the simulator

Requirements: Node 22 or newer.

```bash
npm ci
npm test
npm run simulator
```

Open `http://127.0.0.1:3939`. Join `https://zoom.us/j/123456789`, click the simulator admission endpoint if driving the API, and assign a local task. The simulator never needs a Zoom or OpenAI account.

## Deploy on a dedicated Mac

Robin supports a dedicated Apple-silicon Mac running macOS 14 or newer with a persistent graphical login session. The tested development host is recorded by `npm run doctor`.

```bash
./scripts/bootstrap-macos.sh
./scripts/keychain-secret.sh set OPENAI_API_KEY
./scripts/keychain-secret.sh set ROBIN_PANEL_TOKEN
./scripts/install-launchd.sh
./scripts/doctor.sh
```

Then complete the one-time permission and Zoom-login steps in [Deployment](docs/DEPLOY.md). Do not use a personal everyday desktop: Robin’s isolation and sharing guarantees assume a dedicated OS user and display.

## Commands

- `npm run simulator` — fake Zoom, recorded audio metadata, and deterministic desktop actions.
- `npm test` — policy, audio, lifecycle, stop, redaction, and control API tests.
- `npm run check` — type-check and test.
- `npm run mac-helper:build` — release-build the native helper.
- `npm run doctor` — readiness checks without printing secret values.

See [Architecture](ARCHITECTURE.md), [Security](docs/SECURITY.md), [Demo](docs/DEMO.md), and [Troubleshooting](docs/TROUBLESHOOTING.md).
