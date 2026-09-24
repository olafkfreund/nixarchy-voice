"""Piper's voice, loaded once and kept loaded (#136).

Runs on piper's own interpreter, not ours (OMARCHY_VOICE_PIPER_PYTHON), so it
imports nothing from omarchy_voice: only the standard library and `piper`.
`feedback.PiperWorker` runs it with -P, so this directory is not on sys.path.

The protocol is pipes only. argv[1] is the model. Once it has loaded, it
writes one end marker (a length of 0) to mean "ready". Then each line on
stdin is one sentence, answered with its audio as length-prefixed chunks of
raw int16 PCM, then an end marker. EOF on stdin ends it, so it dies with
whoever holds the other end of the pipe.
"""

import struct
import sys

from piper import PiperVoice

END = struct.pack(">I", 0)


def main() -> None:
    voice = PiperVoice.load(sys.argv[1])
    out = sys.stdout.buffer
    out.write(END)
    out.flush()
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            break
        text = line.decode().strip()
        if text:
            for chunk in voice.synthesize(text):
                audio = chunk.audio_int16_bytes
                if audio:  # a length of 0 is the end marker, never a chunk
                    out.write(struct.pack(">I", len(audio)) + audio)
                    out.flush()
        out.write(END)
        out.flush()


if __name__ == "__main__":
    main()
