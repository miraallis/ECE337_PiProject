#!/usr/bin/env python3
"""
pn532_nfc.py - NFC read / clone driver for the PN532 over I2C.

Part of the ECE337 Pi signal multi-tool. Wraps Adafruit's PN532 CircuitPython
library with the three operations the project demo needs:

    1. read_uid()        - read a tag's UID (the smoke test)
    2. dump_classic()    - read every block of a MIFARE Classic 1K tag
    3. clone_classic()   - write a dumped tag onto a UID-changeable "magic" tag

ETHICS / SCOPE: only ever read or clone tags a team member physically owns, in
the contained lab. Cloning a credential you do not own is outside the project
waiver. Secured cards (payment, modern transit) use dynamic crypto and cannot
be cloned this way - that is a defense point for the report, not a limitation
to work around.

Wiring (PN532 in I2C mode - set the board's DIP switches to I2C):
    VCC -> Pi pin 17 (3.3V)      SDA -> Pi pin 3 (GPIO2)
    GND -> Pi pin 9  (GND)       SCL -> Pi pin 5 (GPIO3)

Setup on the Pi:
    sudo raspi-config         # enable I2C
    pip3 install adafruit-circuitpython-pn532 adafruit-blinka
    i2cdetect -y 1            # confirm the board shows at 0x24

Usage:
    python3 pn532_nfc.py read              # read one UID
    python3 pn532_nfc.py dump  tag.json    # dump a MIFARE Classic tag to a file
    python3 pn532_nfc.py clone tag.json    # write that dump onto a magic tag
"""

import json
import sys
import time

import board
import busio
from digitalio import DigitalInOut
from adafruit_pn532.i2c import PN532_I2C

# Default MIFARE Classic key. Blank/unused sectors on factory tags and most
# "magic" tags ship with this key. Real access cards will use different keys;
# that is the whole point of the NFC defense section.
DEFAULT_KEY = b"\xFF\xFF\xFF\xFF\xFF\xFF"

# MIFARE Classic 1K = 16 sectors x 4 blocks = 64 blocks of 16 bytes.
CLASSIC_1K_BLOCKS = 64
BLOCKS_PER_SECTOR = 4


def get_pn532():
    """Open the I2C bus and return an initialized PN532, or exit with help."""
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        pn532 = PN532_I2C(i2c, debug=False)
        ic, ver, rev, support = pn532.firmware_version
        print(f"[+] Found PN532, firmware {ver}.{rev}")
        pn532.SAM_configuration()  # put the reader into normal "read tags" mode
        return pn532
    except Exception as exc:  # noqa: BLE001 - we want a friendly message here
        print(f"[!] Could not talk to the PN532: {exc}")
        print("    Check: I2C enabled (raspi-config), DIP switches set to I2C,")
        print("    wiring (SDA=pin3, SCL=pin5, VCC=pin17, GND=pin9),")
        print("    and that `i2cdetect -y 1` shows a device at 0x24.")
        sys.exit(1)


def wait_for_tag(pn532, timeout=0.5):
    """Block until a tag is present, return its UID as bytes."""
    print("[*] Hold a tag to the reader...")
    while True:
        uid = pn532.read_passive_target(timeout=timeout)
        if uid is not None:
            return uid


def read_uid(pn532):
    """Read one tag and print its UID. The hardware smoke test."""
    uid = wait_for_tag(pn532)
    uid_hex = uid.hex().upper()
    print(f"[+] UID: {uid_hex}  ({len(uid)} bytes)")
    return uid


def dump_classic(pn532, outfile):
    """Read every block of a MIFARE Classic 1K tag and save to JSON."""
    uid = wait_for_tag(pn532)
    print(f"[+] Dumping tag {uid.hex().upper()}")

    blocks = {}
    for block in range(CLASSIC_1K_BLOCKS):
        sector = block // BLOCKS_PER_SECTOR
        # Authenticate once per sector (on the sector's first block).
        if block % BLOCKS_PER_SECTOR == 0:
            ok = pn532.mifare_classic_authenticate_block(
                uid, block, 0x60, DEFAULT_KEY  # 0x60 = key A
            )
            if not ok:
                print(f"[!] Sector {sector} auth failed (non-default key) - skipping")
                continue
        data = pn532.mifare_classic_read_block(block)
        if data is not None:
            blocks[block] = data.hex()
            print(f"    block {block:2d}: {data.hex()}")

    with open(outfile, "w") as fh:
        json.dump({"uid": uid.hex(), "blocks": blocks}, fh, indent=2)
    print(f"[+] Saved {len(blocks)} blocks to {outfile}")


def clone_classic(pn532, infile):
    """Write a previously dumped tag onto a UID-changeable magic tag."""
    with open(infile) as fh:
        dump = json.load(fh)

    print("[*] Place the blank MAGIC tag on the reader (not the original).")
    target_uid = wait_for_tag(pn532)
    print(f"[+] Writing onto magic tag {target_uid.hex().upper()}")

    written = 0
    for block_str, hexdata in dump["blocks"].items():
        block = int(block_str)

        # Block 0 (manufacturer block, holds the UID) is only writable on a
        # magic tag. Skip the sector-trailer blocks - overwriting the keys/
        # access-bits can brick a sector. Block 0 is handled specially below.
        if block != 0 and (block + 1) % BLOCKS_PER_SECTOR == 0:
            continue  # sector trailer - leave keys/access bits alone

        if block % BLOCKS_PER_SECTOR == 0:
            if not pn532.mifare_classic_authenticate_block(
                target_uid, block, 0x60, DEFAULT_KEY
            ):
                print(f"[!] Auth failed on block {block} - skipping")
                continue

        data = bytes.fromhex(hexdata)
        try:
            pn532.mifare_classic_write_block(block, data)
            written += 1
            print(f"    wrote block {block:2d}")
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Block {block} write failed: {exc}")

    print(f"[+] Clone complete - wrote {written} blocks.")
    print("    Re-read the magic tag and compare its UID/blocks to the original.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("read", "dump", "clone"):
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    pn532 = get_pn532()

    if cmd == "read":
        read_uid(pn532)
    elif cmd == "dump":
        outfile = sys.argv[2] if len(sys.argv) > 2 else "tag.json"
        dump_classic(pn532, outfile)
    elif cmd == "clone":
        if len(sys.argv) < 3:
            print("[!] Need a dump file: python3 pn532_nfc.py clone tag.json")
            sys.exit(1)
        clone_classic(pn532, sys.argv[2])


if __name__ == "__main__":
    main()
