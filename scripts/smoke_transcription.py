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
    output_dir = Path("RobinWorkspace/sessions/speech-openai-smoke")
    bridge = AudioBridge(
        AudioConfig(mode="openai", speech_model="gpt-4o-mini-tts", transcription_model="gpt-4o-mini-transcribe", speech_voice="alloy", speech_format="wav"),
        output_dir,
        os.environ.get("OPENAI_API_KEY"),
    )
    record = await bridge.speak("Robin transcription smoke test complete.")
    if not record.path:
        raise SystemExit("TTS did not produce an audio path")
    text = await bridge.transcribe_file(output_dir / record.path)
    print(f"Transcription smoke passed: {text}")


if __name__ == "__main__":
    asyncio.run(main())
