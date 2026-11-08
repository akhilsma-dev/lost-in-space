"""Every mission-control callout the dashboard can speak: clip id -> text.

The dashboard (dashboard/static/voice.js) picks the clip by id; voice.generate turns
this table into audio files once, so the demo needs no network.
"""

WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen", "twenty"]

RATE_BUCKETS = [0, 5, 10, 15, 20]          # deg/s, knob range 0..20
RELOCK_SECONDS = range(1, 11)              # "lock reacquired in N seconds"
ACCURACY_BUCKETS = [5, 10, 20, 30, 60]     # "pointing error under N arc seconds"


def _lines():
    L = {
        "online": "Mission control online. Star tracker standing by.",
        "lock_first": "Attitude lock acquired.",
        "lock_again": "Lock reacquired.",
        "lost": "Star tracker has lost lock.",
        "search": "Searching the sky.",
        "flare": "Solar flare! Sensor blinded.",
        "unstable": "Lock unstable.",
        "glare_on": "Glare detected.",
        "glare_off": "Glare cleared.",
        "no_signal": "Telemetry lost.",
        "signal_back": "Telemetry restored.",
        # acquisition / recovery (camera.solve_live stages)
        "verifying": "Star pattern found. Verifying.",
        "detumble": "Spinning too fast to acquire. Detumbling.",
        # position from planets (WHERE AM I? on the console)
        "nav_scan": "Scanning for planets.",
        "nav_hold": "Hold still for navigation.",
        "nav_fix": "Position fixed from the planets.",
    }
    for n in RELOCK_SECONDS:
        L[f"relock_{n}"] = f"Lock reacquired in {WORDS[n]} second{'s' if n != 1 else ''}."
    for r in RATE_BUCKETS:
        L[f"rate_{r}"] = ("Spacecraft stabilized." if r == 0
                          else f"Tumble rate {WORDS[r]} degrees per second.")
    for a in ACCURACY_BUCKETS:
        word = "sixty" if a == 60 else "thirty" if a == 30 else WORDS[a]
        L[f"acc_{a}"] = f"Pointing error under {word} arc seconds."
    return L


LINES = _lines()
