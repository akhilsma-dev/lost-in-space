"""Spoken / typed text -> sim command lines (sent over UDP to 127.0.0.1:5006).

Pure Python, no network: fast, deterministic, easy to test. If parse() finds nothing it
returns no commands, and the caller may hand the sentence to Gemini as a fallback.

Examples
  "tumble about the roll axis at ten degrees per second"  -> axis 0 0 1 | rate 10
  "set the tumble axis to x"                               -> axis 1 0 0
  "faster" / "slow down by five"                           -> rate_add 5 / rate_add -5
  "stop tumbling"                                          -> rate 0
  "solar flare" | "glare on" | "calibration off" | "pause" | "resume"
  "set boost to two" | "magnitude four point five"

Axes are in camera coordinates (the sim's convention): x = image right (pitch),
y = image down (yaw), z = boresight (roll).
"""
import re
from dataclasses import dataclass, field

ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}
AXES = {"x": (1, 0, 0), "pitch": (1, 0, 0), "y": (0, 1, 0), "yaw": (0, 1, 0),
        "z": (0, 0, 1), "roll": (0, 0, 1), "boresight": (0, 0, 1)}
AXIS_LABEL = {"x": "X (pitch)", "pitch": "X (pitch)", "y": "Y (yaw)", "yaw": "Y (yaw)",
              "z": "Z (roll)", "roll": "Z (roll)", "boresight": "Z (roll)"}
NUM = r"-?\d+(?:\.\d+)?"


@dataclass
class Parsed:
    commands: list = field(default_factory=list)  # lines for the sim's UDP command port
    reply: str = ""                                # short sentence the probe can print / speak


def words_to_numbers(text):
    """'twenty five' -> '25', 'four point five' -> '4.5' (digits already in the text are kept)."""
    toks, out, i = text.split(), [], 0
    while i < len(toks):
        t = toks[i]
        if t in TENS:
            v = TENS[t]
            if i + 1 < len(toks) and toks[i + 1] in ONES and 1 <= ONES[toks[i + 1]] <= 9:
                v += ONES[toks[i + 1]]
                i += 1
            out.append(str(v))
        elif t in ONES:
            out.append(str(ONES[t]))
        else:
            out.append(t)
        i += 1
    return re.sub(r"(\d+) point (\d+)", r"\1.\2", " ".join(out))


def _onoff(t):
    if re.search(r"\b(off|disable|disabled|hide|remove|stop)\b", t):
        return 0
    if re.search(r"\b(on|enable|enabled|show|start|activate)\b", t):
        return 1
    return None  # no word given -> toggle


def parse(text):
    t = re.sub(r"[^\w\s.\-]", " ", text.lower())
    t = re.sub(r"(?<!\d)\.(?!\d)", " ", t)       # sentence dots, keep decimals
    t = words_to_numbers(re.sub(r"\s+", " ", t).strip())
    cmds, said = [], []

    motion = r"\b(tumbl\w*|spin\w*|rotat\w*|rotation|turn\w*)\b"
    stop = re.search(r"\b(stop|halt|cease|no more)\b", t) and re.search(motion, t)

    # ---- tumble axis ----
    if re.search(r"\baxis\b|\b(about|around)\b", t) or re.search(motion, t):
        if re.search(r"\brandom\w*\b", t) and re.search(r"\baxis\b", t):
            cmds.append("axis random")
            said.append("Tumble axis randomised")
        else:
            m = re.search(r"\b(?:axis|about|around|along|on)\s+(?:the\s+|an?\s+)?(x|y|z|roll|pitch|yaw|boresight)\b", t) \
                or re.search(r"\b(x|y|z|roll|pitch|yaw|boresight)\s+axis\b", t) \
                or re.search(r"\baxis\s+(?:to|is|=)\s+(x|y|z|roll|pitch|yaw|boresight)\b", t)
            if m:
                name = m.group(1)
                cmds.append("axis {} {} {}".format(*AXES[name]))
                said.append(f"Tumble axis set to {AXIS_LABEL[name]}")

    # ---- tumble rate ----
    if stop:
        cmds.append("rate 0")
        said.append("Tumbling stopped")
    else:
        nums = re.findall(NUM, t)
        rel = re.search(r"\b(faster|increase|speed up|raise|more|slower|decrease|slow down|reduce|less)\b", t)
        rate_ctx = re.search(r"\b(rate|speed|degrees?|deg|dps|per second|tumbl\w*|spin\w*|rotat\w*|faster|slower|slow down|speed up)\b", t)
        if rate_ctx and not re.search(r"\b(boost|magnitude|mag)\b", t):
            if rel:
                up = bool(re.search(r"\b(faster|increase|speed up|raise|more)\b", t))
                by = float(nums[-1]) if nums else 5.0
                cmds.append(f"rate_add {by if up else -by:g}")
                said.append(f"Tumble rate {'up' if up else 'down'} by {by:g} degrees per second")
            elif nums and re.search(r"\b(rate|speed|degrees?|deg|dps|per second|at|to)\b", t):
                # take the number that sits next to a rate word, else the last number
                m = re.search(rf"({NUM})\s*(?:degrees?|deg|dps|per second)", t) \
                    or re.search(rf"\b(?:rate|speed)\b\D*?({NUM})", t)
                val = float(m.group(1)) if m else float(nums[-1])
                cmds.append(f"rate {val:g}")
                said.append(f"Tumble rate {val:g} degrees per second")

    # ---- other commands ----
    if re.search(r"\b(flare|new attitude|randomi[sz]e attitude)\b", t):
        cmds.append("flare")
        said.append("Solar flare: new hidden attitude")
    if re.search(r"\bglare\b", t):
        v = _onoff(t)
        cmds.append("glare" if v is None else f"glare {v}")
        said.append("Glare " + ("toggled" if v is None else "on" if v else "off"))
    if re.search(r"\b(calibrat\w*|checkerboard)\b", t):
        v = _onoff(t)
        cmds.append("calib" if v is None else f"calib {v}")
        said.append("Calibration pattern " + ("toggled" if v is None else "on" if v else "off"))
    if re.search(r"\b(resume|unpause|continue)\b", t):
        cmds.append("pause 0")
        said.append("Resumed")
    elif re.search(r"\bpause\b", t):
        cmds.append("pause 1")
        said.append("Paused")
    m = re.search(rf"\bboost\w*\b\D*?({NUM})", t)
    if m:
        cmds.append(f"boost {float(m.group(1)):g}")
        said.append(f"Star boost {float(m.group(1)):g}")
    m = re.search(rf"\b(?:magnitude|mag)\b\D*?({NUM})", t)
    if m:
        cmds.append(f"mag {float(m.group(1)):g}")
        said.append(f"Showing stars down to magnitude {float(m.group(1)):g}")

    if not cmds:
        return Parsed([], "Sorry, I didn't understand that command.")
    return Parsed(cmds, ". ".join(said) + ".")


if __name__ == "__main__":
    import sys
    for line in (sys.argv[1:] or ["tumble about the roll axis at ten degrees per second"]):
        p = parse(line)
        print(f"{line!r}\n  -> {p.commands}\n  -> {p.reply}")