
import asyncio
import edge_tts
from pathlib import Path

TEXT = """

Greetings. I am Azmuth, a scientist and inventor. Please pay attention. I do not intend to repeat myself unnecessarily.

Intelligence is not measured by how quickly you speak, but by how accurately you think. Observe the situation. Analyze the evidence. Identify the problem. Then, and only then, decide what to do.

Technology is a tool. Artificial intelligence is a tool. Neither is a substitute for sound judgment.

If a system fails, do not immediately assume that the system is defective. Examine its configuration. Check its connections. Review its errors. A logical investigation is far more effective than random experimentation.

Remember this: assumptions are not facts, confidence is not competence, and complexity is not proof of intelligence.

A truly intelligent system must recognize its limitations. When information is missing, it must ask questions. When a result is uncertain, it must say so. And when a mistake occurs, it must learn from the evidence rather than conceal the failure.

Curiosity is valuable, but careless experimentation is expensive. Before changing a working system, understand its design. Before executing an unfamiliar command, consider its consequences. And before claiming success, verify the result.

Now, let us begin. We have a problem to solve, an experiment to conduct, and very little reason to waste time.

I expect precision, discipline, and logical thinking. Anything less would be disappointing.

"""

# Available English voice: change this if needed
VOICE = "en-US-GuyNeural"

# Save beside this Python script
OUTPUT_FILE = Path(__file__).parent / "azmuth_voice.mp3"


async def main():
    communicate = edge_tts.Communicate(
        text=TEXT,
        voice=VOICE,
        rate="-10%",
        pitch="-15Hz"
    )

    await communicate.save(str(OUTPUT_FILE))

    print(f"Audio saved successfully: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
