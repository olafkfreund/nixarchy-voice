#!/usr/bin/env python3
"""Record the demo on razer, check it, and cut the media from it (#163).

    demo/demo.py prep       refuse on a busy desktop; save its state; set up
    demo/demo.py record     one take, driven by shots.toml
    demo/demo.py verify     every beat shows what it claims (raw master only)
    demo/demo.py edit       mp4, poster, stills and nixarchy's GIF
    demo/demo.py restore    put razer back; safe to re-run

Everything runs for real on razer through demo/rz. Output goes to demo/out/
(not committed) except what `edit` writes into docs/media/.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
MEDIA = ROOT / "docs" / "media"
SHOTS = tomllib.loads((HERE / "shots.toml").read_text())["beat"]
STATE = "/tmp/oma-demo-state.json"          # on razer
MASTER = "/tmp/oma-demo-master.mp4"          # on razer
LOCAL_MASTER = OUT / "master.mp4"
BEATS = OUT / "beats.json"
TAKE_WS, FOCUS_WS = "7", "8"
# razer's tmux keeps its socket under XDG_RUNTIME_DIR, not /tmp: a bare `tmux`
# over ssh looked in the wrong place, and restore's kill-session missed Oma.
TMUX = "tmux -S /run/user/$(id -u)/tmux-$(id -u)/default"
DEMO_ACTIONS = ("demo-focus", "demo-hello")

HELLO = """\
# Demo routine (#163): fires once during the take, removed by restore.
description = "Demo routine"

[[step]]
tool = "system_query"
args = { topic = "time" }

[schedule]
when = "*-*-* 00:00:00"
enabled = false
"""
MAKEFILE = "test:\n\t@echo 'running 3 tests'; echo '1 failed'; exit 1\n"


def rz(cmd: str, check: bool = True, timeout: float = 120) -> str:
    r = subprocess.run([str(HERE / "rz"), cmd], capture_output=True, text=True,
                       timeout=timeout)
    if check and r.returncode:
        sys.exit(f"on razer: {cmd}\n{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def dispatch(lua: str) -> None:
    rz(f"hyprctl dispatch {shlex.quote(lua)}")


# -- prep / restore -----------------------------------------------------------
def prep() -> None:
    clients = json.loads(rz("hyprctl clients -j"))
    if clients:
        names = ", ".join(f"{c['class']} ({c['title'][:30]})" for c in clients)
        # Refuse, never tidy: closing someone's windows is not recoverable.
        sys.exit(f"razer has open windows; close them first: {names}")
    if rz(f"test -e {STATE} && echo yes", check=False) == "yes":
        sys.exit(f"{STATE} exists: a previous take was not restored. Run restore first.")
    state = {
        "theme": rz("omarchy theme current"),
        "bg": rz("omarchy theme bg current", check=False),
        "workspace": json.loads(rz("hyprctl activeworkspace -j"))["name"],
        "listening": json.loads(rz("omarchy-voice status --json")).get("status"),
    }
    rz(f"echo {shlex.quote(json.dumps(state))} > {STATE}")
    print("saved", state)
    rz("rm -rf /tmp/oma-demo && mkdir -p /tmp/oma-demo && cd /tmp/oma-demo && git init -q"
       f" && printf %s {shlex.quote(MAKEFILE)} > Makefile && echo demo > README.md")
    rz("mkdir -p ~/.config/omarchy-voice/actions && printf %s "
       f"{shlex.quote(HELLO)} > ~/.config/omarchy-voice/actions/demo-hello.toml"
       " && omarchy-voice action list >/dev/null")
    if state["theme"] != "Tokyo Night":
        rz("omarchy theme set 'Tokyo Night'", timeout=180)
    dispatch(f'hl.dsp.focus({{ workspace = "{TAKE_WS}" }})')
    print("razer ready: Tokyo Night, workspace", TAKE_WS, "demo repo and demo-hello in place")


def restore() -> None:
    raw = rz(f"cat {STATE}", check=False)
    state = json.loads(raw) if raw else None
    # Close the menu only if it is open: toggling a closed one opens it.
    rz("hyprctl layers | grep -q 'namespace: omarchy-menu' && "
       "omarchy-shell shell toggle omarchy.menu >/dev/null 2>&1; true", check=False)
    for c in json.loads(rz("hyprctl clients -j")):
        # prep refused unless razer had no windows, so every window now is ours
        dispatch(f'hl.dsp.window.close({{ window = "address:{c["address"]}" }})')
    for name in DEMO_ACTIONS:
        rz(f"omarchy-voice action disable {name} >/dev/null 2>&1; "
           f"omarchy-voice action delete {name} >/dev/null 2>&1; true", check=False)
    rz("rm -f ~/.config/omarchy-voice/actions/.trash/demo-*; "
       "rmdir ~/.config/omarchy-voice/actions/.trash 2>/dev/null; "
       f"{TMUX} kill-session -t Oma 2>/dev/null; rm -rf /tmp/oma-demo; "
       "f=~/.local/state/omarchy-voice/approvals.json; "
       "[ \"$(cat $f 2>/dev/null)\" = '{}' ] && rm -f $f; true", check=False)
    if state:
        if rz("omarchy theme current") != state["theme"]:
            rz(f"omarchy theme set {shlex.quote(state['theme'])}", timeout=180)
        if state.get("bg") and rz("omarchy theme bg current", check=False) != state["bg"]:
            rz(f"omarchy theme bg set {shlex.quote(state['bg'])}", check=False)
        dispatch(f'hl.dsp.focus({{ workspace = "{state["workspace"]}" }})')
        if state.get("listening") != "listening":
            rz("omarchy-voice listen stop", check=False)
    # Re-read the live state rather than trusting the steps above.
    left = rz("ls ~/.config/omarchy-voice/actions 2>/dev/null | grep '^demo-'; "
              "ls ~/.config/systemd/user 2>/dev/null | grep 'omarchy-voice-routine-demo'; "
              "test -e /tmp/oma-demo && echo /tmp/oma-demo; "
              f"{TMUX} has-session -t Oma 2>/dev/null && echo 'tmux session Oma'; true",
              check=False)
    theme = rz("omarchy theme current")
    failed = rz("systemctl --failed --no-legend | wc -l; systemctl --user --failed --no-legend | wc -l")
    problems = []
    if left:
        problems.append(f"left behind: {left}")
    if state and theme != state["theme"]:
        problems.append(f"theme is {theme}, wanted {state['theme']}")
    if failed.split() != ["0", "0"]:
        problems.append(f"failed units (system, user): {failed.split()}")
    if problems:
        sys.exit("restore incomplete: " + "; ".join(problems))
    rz(f"rm -f {STATE}", check=False)
    print(f"razer restored: theme {theme}, nothing left behind, 0 failed units")


# -- record -------------------------------------------------------------------
def record() -> None:
    if rz(f"test -e {STATE} && echo yes", check=False) != "yes":
        sys.exit("run prep first")
    OUT.mkdir(exist_ok=True)
    rz(f"rm -f {MASTER}; setsid gpu-screen-recorder -w eDP-1 -f 30 -a default_output "
       f"-c mp4 -o {MASTER} >/tmp/gsr.log 2>&1 < /dev/null & echo $! > /tmp/gsr.pid")
    time.sleep(2.5)            # the recorder reaches steady state (nixarchy #930)
    t0 = time.monotonic() - 2.5
    beats = []
    for beat in SHOTS:
        run = beat["run"]
        if "{when}" in run:
            fire = (datetime.now() + timedelta(minutes=1)).replace(second=0, microsecond=0)
            if (fire - datetime.now()).total_seconds() < 25:
                fire += timedelta(minutes=1)
            run = run.replace("{when}", fire.strftime("*-*-* %H:%M:00"))
        start = time.monotonic() - t0
        rz(run, check=False)
        time.sleep(beat["hold"])
        beats.append({"name": beat["name"], "start": round(start, 2),
                      "end": round(time.monotonic() - t0, 2)})
        print(f"  {beat['name']:14} {beats[-1]['start']:6.1f} → {beats[-1]['end']:6.1f}")
    rz("kill -INT $(cat /tmp/gsr.pid); sleep 3; ls -la " + MASTER)
    subprocess.run(["scp", "-q", f"razer:{MASTER}", str(LOCAL_MASTER)], check=True)
    BEATS.write_text(json.dumps(beats, indent=1))
    print(f"master: {LOCAL_MASTER} ({LOCAL_MASTER.stat().st_size >> 20} MB)")


# -- verify -------------------------------------------------------------------
def ff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", *args],
                          capture_output=True, text=True)


def squash(text: str) -> str:
    """Lowercase letters and digits only: OCR spacing and punctuation vary."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def ocr(png: Path) -> str:
    """Light text on a dark desktop reads badly at native size: tesseract read
    past a highlighted `demo-focus` row. Scaled 2x, grey and inverted, it is
    dark-on-light, which is what it is trained on. The kept frame is untouched."""
    prepared = png.with_suffix(".ocr.png")
    ff("-y", "-i", str(png), "-vf", "scale=iw*2:-1:flags=lanczos,format=gray,negate",
       str(prepared))
    text = subprocess.run(["tesseract", str(prepared), "-", "--psm", "3"],
                          capture_output=True, text=True).stdout
    prepared.unlink(missing_ok=True)
    return text


def verify() -> None:
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                              "-of", "csv=p=0", str(LOCAL_MASTER)],
                             capture_output=True, text=True).stdout.split()
    if "subtitle" in streams or (OUT / "captioned").exists():
        sys.exit("refusing: verify runs on the raw master, before captions")
    beats = {b["name"]: b for b in json.loads(BEATS.read_text())}
    frames = OUT / "beats"
    frames.mkdir(exist_ok=True)
    failed = []
    for shot in SHOTS:
        b = beats[shot["name"]]
        if shot.get("expect"):
            found = None
            for t in range(int(b["start"]), int(b["end"]) + 1):
                png = frames / f"{shot['name']}-{t:04d}.png"
                ff("-y", "-ss", str(t), "-i", str(LOCAL_MASTER), "-frames:v", "1", str(png))
                if squash(shot["expect"]) in squash(ocr(png)):
                    found = png
                    break
                png.unlink(missing_ok=True)
            if found:
                found.rename(frames / f"{shot['name']}.png")
                b["seen"] = int(found.stem.rsplit("-", 1)[1])
                print(f"  ✓ {shot['name']:14} '{shot['expect']}' at {b['seen']}s")
            else:
                failed.append(f"{shot['name']}: '{shot['expect']}' never on screen")
                print(f"  ✗ {shot['name']:14} '{shot['expect']}' not found")
        if shot.get("expect_audio"):
            # volumedetect reports at info level; ff()'s -v error would hide it
            r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(b["start"]), "-to",
                                str(b["end"]), "-i", str(LOCAL_MASTER), "-af", "volumedetect",
                                "-vn", "-f", "null", "-"], capture_output=True, text=True)
            m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
            mean = float(m.group(1)) if m else -99.0
            if mean > -45:
                print(f"  ✓ {shot['name']:14} voice heard (mean {mean} dB)")
            else:
                failed.append(f"{shot['name']}: silent (mean {mean} dB)")
                print(f"  ✗ {shot['name']:14} silent (mean {mean} dB)")
    BEATS.write_text(json.dumps(list(beats.values()), indent=1))
    if failed:
        sys.exit("verify failed:\n  " + "\n  ".join(failed))
    print("verify: every beat shows what it claims")


# -- edit ---------------------------------------------------------------------
def font() -> str:
    return subprocess.run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"],
                          capture_output=True, text=True).stdout.strip()


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def edit() -> None:
    beats = {b["name"]: b for b in json.loads(BEATS.read_text())}
    if not all("seen" in beats[s["name"]] for s in SHOTS if s.get("expect")):
        sys.exit("run verify first")
    MEDIA.mkdir(parents=True, exist_ok=True)
    f = font()
    cap = ",".join(
        f"drawtext=fontfile='{f}':text='{esc(s['caption'])}':fontsize=34:fontcolor=white:"
        f"box=1:boxcolor=black@0.62:boxborderw=18:x=(w-text_w)/2:y=h-150:"
        f"enable='between(t,{beats[s['name']]['start']},{beats[s['name']]['end']})'"
        for s in SHOTS)
    captioned = OUT / "captioned.mp4"
    r = ff("-y", "-i", str(LOCAL_MASTER), "-vf", cap, "-af", "loudnorm=I=-16:TP=-1.5",
           "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-b:a", "128k", str(captioned))
    if r.returncode:
        sys.exit(r.stderr)
    (OUT / "captioned").touch()
    card = OUT / "card.mp4"
    ff("-y", "-f", "lavfi", "-i", "color=c=0x1a1b26:s=1920x1080:d=4:r=30",
       "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "4",
       "-vf", f"drawtext=fontfile='{f}':text='Oma — voice for Nixarchy':fontsize=64:"
              "fontcolor=0xc0caf5:x=(w-text_w)/2:y=h/2-70,"
              f"drawtext=fontfile='{f}':text='olafkfreund.github.io/nixarchy-voice':"
              "fontsize=40:fontcolor=0x7aa2f7:x=(w-text_w)/2:y=h/2+20",
       "-c:v", "libx264", "-c:a", "aac", str(card))
    listing = OUT / "concat.txt"
    listing.write_text(f"file '{captioned}'\nfile '{card}'\n")
    mp4 = MEDIA / "oma-demo.mp4"
    r = ff("-y", "-f", "concat", "-safe", "0", "-i", str(listing),
           "-vf", "scale=1280:720:flags=lanczos", "-c:v", "libx264", "-crf", "28",
           "-preset", "slow", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(mp4))
    if r.returncode:
        sys.exit(r.stderr)
    stills = {"readback": "ask", "actions-menu": "confirm", "terminal": "run-from-menu",
              "routine-notification": "routine"}
    for out, name in stills.items():
        ff("-y", "-ss", str(beats[name]["seen"]), "-i", str(LOCAL_MASTER),
           "-frames:v", "1", str(MEDIA / f"{out}.png"))
    ff("-y", "-ss", str(beats["run-from-menu"]["seen"]), "-i", str(LOCAL_MASTER),
       "-frames:v", "1", "-vf", "scale=1280:-1", "-q:v", "3", str(MEDIA / "oma-demo.jpg"))
    # nixarchy's GIF (#1022): 16:10 centre crop, its encode-gif.sh settings, 96 colours.
    a, z = beats["confirm"]["start"], beats["run-from-menu"]["end"]
    gif = OUT / "voice.gif"
    ff("-y", "-ss", str(a), "-to", str(z), "-i", str(LOCAL_MASTER), "-vf",
       "crop=1728:1080,fps=4,scale=900:-1:flags=lanczos,split[a][b];"
       "[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer", str(gif))
    for p in sorted([*MEDIA.iterdir(), gif]):
        print(f"  {p.name:28} {p.stat().st_size / 1e6:6.2f} MB")
    print(f"  docs/media total: {sum(p.stat().st_size for p in MEDIA.iterdir()) / 1e6:.2f} MB")


if __name__ == "__main__":
    cmds = {"prep": prep, "record": record, "verify": verify, "edit": edit, "restore": restore}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
