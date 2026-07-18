.PHONY: setup seed seed-demo dev doctor preflight core web test smoke smoke-test smoke-audio smoke-bridge smoke-capture smoke-listen smoke-leave-cleanup smoke-meet-fixture smoke-meet-recovery smoke-share-dialog-fixture smoke-calendar smoke-observability smoke-workspace smoke-retry-present smoke-validation smoke-clarification smoke-queue smoke-dedup smoke-real-meet demo-reset typecheck

setup:
	scripts/setup_partner.sh

seed:
	uv run python scripts/seed_demo_workspace.py

seed-demo: seed

dev:
	uv run python scripts/robin.py dev

doctor:
	uv run python scripts/robin.py doctor

preflight: doctor

core:
	uv run uvicorn robin_core.main:app --app-dir apps/core --reload --host 127.0.0.1 --port 8787

web:
	pnpm --dir apps/web dev

test:
	uv run pytest
	pnpm --dir apps/web test

smoke:
	uv run python scripts/smoke_demo.py

smoke-test: smoke

smoke-audio:
	uv run python scripts/smoke_tts.py
	uv run python scripts/smoke_transcription.py

smoke-bridge:
	uv run python scripts/smoke_bridge.py

smoke-capture:
	uv run python scripts/smoke_capture.py

smoke-listen:
	uv run python scripts/smoke_listen_loop.py

smoke-leave-cleanup:
	uv run python scripts/smoke_leave_cleanup.py

smoke-meet-fixture:
	uv run python scripts/smoke_meet_fixture.py

smoke-meet-recovery:
	uv run python scripts/smoke_meet_recovery.py

smoke-share-dialog-fixture:
	uv run python scripts/smoke_share_dialog_fixture.py

smoke-calendar:
	uv run python scripts/smoke_calendar.py

smoke-observability:
	uv run python scripts/smoke_observability.py

smoke-workspace:
	uv run python scripts/smoke_workspace.py

smoke-retry-present:
	uv run python scripts/smoke_retry_present.py

smoke-validation:
	uv run python scripts/smoke_validation.py

smoke-clarification:
	uv run python scripts/smoke_clarification.py

smoke-queue:
	uv run python scripts/smoke_queue.py

smoke-dedup:
	uv run python scripts/smoke_dedup.py

smoke-real-meet:
	uv run python scripts/smoke_real_meet.py

demo-reset:
	uv run python scripts/demo_reset.py --start

typecheck:
	pnpm --dir apps/web typecheck
