from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "core"))

from robin_core.audio.bridge import AudioBridge
from robin_core.config import AudioConfig


async def main() -> None:
    load_dotenv(".env")
    bridge = AudioBridge(
        AudioConfig(mode="openai", speech_model="gpt-4o-mini-tts", speech_voice="alloy", speech_format="wav"),
        Path("RobinWorkspace/sessions/speech-openai-smoke"),
        os.environ.get("OPENAI_API_KEY"),
    )
    record = await bridge.speak("Robin audio smoke test complete.")
    print(f"TTS smoke passed: {record.byte_count} bytes at {record.path}")


if __name__ == "__main__":
    asyncio.run(main())

