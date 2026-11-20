#!/usr/bin/env python3
"""
subghz_capture_replay.py - capture and replay OOK sub-GHz signals with the CC1101.

Part of the ECE337 Pi signal multi-tool. This is the garage-remote demo:
capture the raw on/off-keyed pulse train from a remote you own, then replay it.

    - Fixed-code remote  -> replay reproduces the same code -> receiver reacts.
    - Rolling-code remote -> replay sends a stale code       -> receiver ignores it.

That success/failure contrast IS the experiment. A replay that fails on a
rolling-code device is a correct result, not a bug: it demonstrates the defense.

ETHICS / SCOPE: capture and replay ONLY against devices a team member owns
(your own garage opener, your own test remote) in the contained lab. Do not
transmit on a neighbour's opener, a car, or any device you do not own - that is
outside the project waiver and can be illegal. Never attempt to defeat a
rolling-code system (jam-and-capture, relay, amplification are out of scope).

WHY pigpio: capture and replay are really GPIO-timing jobs (the CC1101 just acts
as the radio front-end in asynchronous serial mode - see cc1101.py). Plain
Python GPIO sampling is too jittery for clean ~250-500 us OOK pulses. pigpio
timestamps edges and replays waveforms with microsecond precision, which is what
makes a captured garage code actually reproducible.

GDO0 (Pi GPIO25, pin 22) is the data line: the Pi READS it during capture
(CC1101 in RX mirrors the demodulated signal onto it) and DRIVES it during
replay (CC1101 in async TX transmits whatever the Pi puts on it).

Setup on the Pi:
    sudo apt install pigpio python3-pigpio
    sudo systemctl enable --now pigpiod      # start the pigpio daemon
    pip3 install spidev

Usage:
    python3 subghz_capture_replay.py capture code.json [--freq 433.92]
    python3 subghz_capture_replay.py replay  code.json [--freq 433.92] [--repeat 5]
"""

import argparse
import json
import sys
import time

import pigpio

from cc1101 import CC1101

GDO0_GPIO = 25  # BCM numbering -> physical pin 22

# Capture tuning. Pulses shorter than this (noise glitches) are dropped; a gap
# longer than this ends the capture (the remote stopped transmitting).
MIN_PULSE_US = 80
END_GAP_US = 15000  # 15 ms of silence = end of the burst
MAX_PULSES = 5000


def _require_pigpio():
    pi = pigpio.pi()
    if not pi.connected:
        print("[!] Can't reach the pigpio daemon. Start it with:")
        print("    sudo systemctl enable --now pigpiod")
        sys.exit(1)
    return pi


def capture(outfile, freq):
    """Record the OOK pulse train from a remote into a JSON file.

    Stores a list of [level, duration_us] pairs - the raw timing of the signal,
    which is all a fixed-code replay needs.
    """
    radio = CC1101()
    radio.configure_ook()
    radio.set_frequency(freq)
    radio.receive_mode()

    pi = _require_pigpio()
    pi.set_mode(GDO0_GPIO, pigpio.INPUT)

    pulses = []
    state = {"last_tick": None, "last_level": pi.read(GDO0_GPIO)}

    def on_edge(gpio, level, tick):
        if level == pigpio.TIMEOUT:
            return
        if state["last_tick"] is not None:
            # pigpio ticks are microseconds; subtraction handles 32-bit wrap.
            dur = pigpio.tickDiff(state["last_tick"], tick)
            if dur >= MIN_PULSE_US:
                pulses.append([state["last_level"], dur])
        state["last_tick"] = tick
        state["last_level"] = level

    cb = pi.callback(GDO0_GPIO, pigpio.EITHER_EDGE, on_edge)

    print(f"[*] Listening on {freq} MHz. Press your remote once or twice...")
    print("    (Ctrl-C to stop early.)")
    try:
        # Wait until we've seen a burst followed by a long gap of silence.
        last_count = 0
        idle_loops = 0
        while len(pulses) < MAX_PULSES:
            time.sleep(0.2)
            if len(pulses) == last_count and last_count > 0:
                idle_loops += 1
                if idle_loops >= 3:  # ~600 ms with no new edges -> done
                    break
            else:
                idle_loops = 0
            last_count = len(pulses)
    except KeyboardInterrupt:
        pass
    finally:
        cb.cancel()
        radio.idle()
        radio.close()
        pi.stop()

    if not pulses:
        print("[!] No signal captured. Check: remote battery, correct frequency")
        print("    (try --freq 315), and GDO0 wired to pin 22 (GPIO25).")
        sys.exit(1)

    with open(outfile, "w") as fh:
        json.dump({"freq_mhz": freq, "pulses": pulses}, fh)
    total_ms = sum(p[1] for p in pulses) / 1000.0
    print(f"[+] Captured {len(pulses)} pulses ({total_ms:.1f} ms) -> {outfile}")


def replay(infile, freq, repeat):
    """Replay a captured pulse train by driving GDO0 while the CC1101 transmits."""
    with open(infile) as fh:
        data = json.load(fh)
    pulses = data["pulses"]
    freq = freq or data.get("freq_mhz", 433.92)

    radio = CC1101()
    radio.configure_ook()
    radio.set_frequency(freq)

    pi = _require_pigpio()
    pi.set_mode(GDO0_GPIO, pigpio.OUTPUT)
    pi.write(GDO0_GPIO, 0)

    # Build one pigpio waveform from the captured pulse timings.
    pi.wave_clear()
    wf = []
    for level, dur in pulses:
        if level:
            wf.append(pigpio.pulse(1 << GDO0_GPIO, 0, dur))      # GDO0 high
        else:
            wf.append(pigpio.pulse(0, 1 << GDO0_GPIO, dur))      # GDO0 low
    pi.wave_add_generic(wf)
    wave_id = pi.wave_create()

    print(f"[*] Transmitting on {freq} MHz, {repeat}x...")
    radio.transmit_mode()
    try:
        for i in range(repeat):
            pi.wave_send_once(wave_id)
            while pi.wave_tx_busy():
                time.sleep(0.001)
            pi.write(GDO0_GPIO, 0)
            time.sleep(0.25)  # inter-burst gap, like a real remote
            print(f"    sent {i + 1}/{repeat}")
    finally:
        pi.wave_delete(wave_id)
        radio.idle()
        radio.close()
        pi.stop()

    print("[+] Replay done.")
    print("    Fixed-code device: it should have reacted. Rolling-code device:")
    print("    nothing happens - that is the defense working. Record both.")


def main():
    ap = argparse.ArgumentParser(description="CC1101 OOK capture/replay")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("capture")
    pc.add_argument("file")
    pc.add_argument("--freq", type=float, default=433.92)

    pr = sub.add_parser("replay")
    pr.add_argument("file")
    pr.add_argument("--freq", type=float, default=None)
    pr.add_argument("--repeat", type=int, default=5)

    args = ap.parse_args()
    if args.cmd == "capture":
        capture(args.file, args.freq)
    elif args.cmd == "replay":
        replay(args.file, args.freq, args.repeat)


if __name__ == "__main__":
    main()
