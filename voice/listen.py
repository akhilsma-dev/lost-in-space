"""Voice control for the sky sim: microphone -> ElevenLabs speech-to-text -> sim commands.

Run (from the project root, with the sim already running):
  .venv/bin/python -m voice.listen                      press Enter, speak ~4 s, repeat (needs a microphone)
  .venv/bin/python -m voice.listen --file command.wav   transcribe a recording instead
  .venv/bin/python -m voice.listen --text "stop tumbling" --dry-run    no audio, no network: test the parser

Setup:  pip install elevenlabs sounddevice numpy
        export ELEVENLABS_API_KEY=...        (never put the key in the code or the repo)

The transcript is turned into commands by voice/commands.py (rules, deterministic) and sent as
UDP text lines to the sim on 127.0.0.1:5006. Nothing the speech model hears can run anything
except that short list of commands, and the sim clips values (e.g. tumble rate 0-60 deg/s).
"""
import argparse
import io
import os
import socket
import wave

from voice.commands import parse

# words the sim cares about; helps the speech model spell them right
KEYTERMS = ["tumble", "axis", "flare", "glare", "calibration", "boost", "magnitude", "roll", "pitch", "yaw"]


def send(lines, port, dry):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for line in lines:
        print(f"  -> sim: {line}" + ("   (dry run, not sent)" if dry else ""))
        if not dry:
            sock.sendto(line.encode(), ("127.0.0.1", port))


def record_wav(seconds, rate=16000):
    import numpy as np
    import sounddevice as sd
    print(f"  recording {seconds:g} s ... speak now")
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(audio, dtype="int16").tobytes())
    return buf.getvalue()


def transcribe(client, audio_bytes, filename="speech.wav"):
    """Batch speech-to-text with ElevenLabs Scribe v2. Returns the transcript text."""
    def as_file():
        f = io.BytesIO(audio_bytes)
        f.name = filename  # lets the API tell wav / webm / mp4 apart
        return f
    try:
        result = client.speech_to_text.convert(file=as_file(), model_id="scribe_v2",
                                               language_code="eng", keyterms=KEYTERMS)
    except Exception as e:  # the keyterms option may not exist on every plan/SDK version
        print(f"  (retrying without keyterms: {type(e).__name__})")
        result = client.speech_to_text.convert(file=as_file(), model_id="scribe_v2",
                                               language_code="eng")
    return result.text


def handle(text, port, dry):
    print(f'heard: "{text}"')
    p = parse(text)
    print(f"  probe: {p.reply}")
    send(p.commands, port, dry)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", help="skip audio: parse this sentence")
    ap.add_argument("--file", help="transcribe this audio file (wav/mp3) instead of the microphone")
    ap.add_argument("--seconds", type=float, default=4.0, help="recording length per command")
    ap.add_argument("--port", type=int, default=5006, help="sim command port")
    ap.add_argument("--dry-run", action="store_true", help="print the commands but do not send them")
    args = ap.parse_args()

    if args.text:
        handle(args.text, args.port, args.dry_run)
        return

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("set ELEVENLABS_API_KEY first (export ELEVENLABS_API_KEY=...)")
    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(api_key=key)

    if args.file:
        with open(args.file, "rb") as f:
            handle(transcribe(client, f.read()), args.port, args.dry_run)
        return

    print("Voice control ready. Try: 'tumble about the roll axis at ten degrees per second', "
          "'stop tumbling', 'solar flare', 'glare on'.")
    while True:
        try:
            if input("\nPress Enter and speak (q + Enter to quit): ").strip().lower() == "q":
                break
            handle(transcribe(client, record_wav(args.seconds)), args.port, args.dry_run)
        except (KeyboardInterrupt, EOFError):
            break
        except Exception as e:
            print(f"  error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()