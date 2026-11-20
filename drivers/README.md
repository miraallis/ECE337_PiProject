# Pi signal multi-tool — drivers

Drivers for the ECE337 ethical-hacking project: a Raspberry Pi with a CC1101
sub-GHz radio and a PN532 NFC reader, used to demonstrate wireless replay and
cloning vulnerabilities and their defenses.

**Scope / ethics:** every capture, replay, or clone runs only against hardware a
team member owns, in the contained lab, under the signed waiver. No transmitting
on devices or frequencies you don't own; no attempts to defeat rolling-code
systems. The goal is to measure and document, not to gain entry.

## Files

| File | What it does |
|------|--------------|
| `pn532_nfc.py` | Read a tag UID, dump a MIFARE Classic tag, clone it to a magic tag |
| `cc1101.py` | Register-level CC1101 control (SPI): reset, config, RX/TX modes |
| `subghz_capture_replay.py` | Capture an OOK remote signal and replay it |
| `requirements.txt` | Python + system dependencies |

## One-time Pi setup

```bash
sudo raspi-config                        # Interface Options -> enable SPI and I2C
sudo apt install pigpio python3-pigpio
sudo systemctl enable --now pigpiod
pip3 install -r requirements.txt
```

## Bring-up order (test one module at a time)

**1. CC1101 SPI smoke test** — confirms the Pi talks to the chip:
```bash
python3 cc1101.py          # expect VERSION=0x14
```

**2. PN532 NFC** (set the board's DIP switches to I2C first):
```bash
i2cdetect -y 1             # expect a device at 0x24
python3 pn532_nfc.py read              # read a tag UID
python3 pn532_nfc.py dump  mytag.json  # dump a tag you own
python3 pn532_nfc.py clone mytag.json  # write it onto a magic tag
```

**3. Sub-GHz capture / replay** (against your own garage remote / test remote):
```bash
python3 subghz_capture_replay.py capture code.json            # press remote
python3 subghz_capture_replay.py replay  code.json --repeat 5
# If your remote is 315 MHz (check its FCC label): add --freq 315
```

## Wiring (quick reference)

CC1101 (3.3V only — never 5V): VCC→pin1, GND→pin6, MOSI→pin19, MISO→pin21,
SCLK→pin23, CSN→pin24, GDO0→pin22, GDO2→pin18.

PN532 (I2C mode): VCC→pin17, GND→pin9, SDA→pin3, SCL→pin5.

## Notes / gotchas

- **CC1101 timing is the hard part.** Capture/replay depends on microsecond GPIO
  timing, which is why it uses `pigpio` (the daemon must be running). If a
  captured code won't replay, first confirm the frequency (315 vs 433.92 MHz),
  then check that the capture actually recorded a clean burst (open the JSON —
  you should see a few dozen to a few hundred pulses, not two).
- **Rolling-code replay is supposed to fail.** Don't "fix" it — record the
  failure; it's the defense demonstration.
- **PN532 clone only works onto UID-changeable "magic" tags.** Factory tags have
  a locked block 0.
