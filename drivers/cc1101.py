#!/usr/bin/env python3
"""
cc1101.py - register-level driver for the CC1101 sub-GHz transceiver over SPI.

Part of the ECE337 Pi signal multi-tool. This file is ONLY the chip control
layer: reset, strobe commands, register read/write, frequency + modulation
config, and RX/TX mode switching. The actual capture/replay of a garage-remote
signal lives in subghz_capture_replay.py, which imports this class.

It configures the CC1101 for ASYNCHRONOUS SERIAL mode with OOK/ASK modulation -
the mode you need to capture and replay the raw on/off keying that cheap
fixed-code remotes use. In this mode the chip does no packet handling: in RX it
just mirrors the demodulated signal onto the GDO0 pin, and in TX it transmits
whatever you drive onto GDO0. That is why capture/replay is really a GPIO-timing
job (see the other file), with the CC1101 acting as the radio front-end.

Wiring (CC1101 is 3.3V ONLY - never 5V):
    VCC  -> Pi pin 1  (3.3V)       SCLK -> Pi pin 23 (GPIO11, SPI0 SCLK)
    GND  -> Pi pin 6  (GND)        MISO -> Pi pin 21 (GPIO9,  SPI0 MISO)
    MOSI -> Pi pin 19 (GPIO10)     CSN  -> Pi pin 24 (GPIO8,  SPI0 CE0)
    GDO0 -> Pi pin 22 (GPIO25)     GDO2 -> Pi pin 18 (GPIO24)

Setup on the Pi:
    sudo raspi-config         # enable SPI
    pip3 install spidev RPi.GPIO

Note: the register values below set 433.92 MHz with ASK/OOK. If your remote is
on 315 MHz (check its FCC label), call set_frequency(315.0) before RX/TX.
"""

import time

import spidev

# --- CC1101 header byte bits ------------------------------------------------
WRITE_SINGLE = 0x00
WRITE_BURST = 0x40
READ_SINGLE = 0x80
READ_BURST = 0xC0

# --- Config registers (subset we use) ---------------------------------------
IOCFG2 = 0x00
IOCFG0 = 0x02
FIFOTHR = 0x03
PKTCTRL0 = 0x08
FSCTRL1 = 0x0B
FREQ2 = 0x0D
FREQ1 = 0x0E
FREQ0 = 0x0F
MDMCFG4 = 0x10
MDMCFG3 = 0x11
MDMCFG2 = 0x12
MDMCFG1 = 0x13
MDMCFG0 = 0x14
MCSM0 = 0x18
FOCCFG = 0x19
AGCCTRL2 = 0x1B
AGCCTRL1 = 0x1C
AGCCTRL0 = 0x1D
FREND1 = 0x21
FREND0 = 0x22
FSCAL3 = 0x23
FSCAL2 = 0x24
FSCAL1 = 0x25
FSCAL0 = 0x26
TEST2 = 0x2C
TEST1 = 0x2D
TEST0 = 0x2E
PATABLE = 0x3E

# --- Command strobes --------------------------------------------------------
SRES = 0x30  # reset chip
SRX = 0x34  # enable RX
STX = 0x35  # enable TX
SIDLE = 0x36  # idle
SFRX = 0x3A  # flush RX FIFO
SFTX = 0x3B  # flush TX FIFO

# --- Status registers -------------------------------------------------------
PARTNUM = 0x30
VERSION = 0x31
MARCSTATE = 0x35


class CC1101:
    """Minimal CC1101 control over SPI (spidev)."""

    def __init__(self, bus=0, device=0, speed_hz=50000):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = speed_hz
        self.spi.mode = 0b00  # CC1101 is SPI mode 0

    # -- low-level SPI --------------------------------------------------------
    def _strobe(self, cmd):
        return self.spi.xfer([cmd])[0]

    def write_reg(self, addr, value):
        self.spi.xfer([addr | WRITE_SINGLE, value])

    def read_reg(self, addr):
        # Status/command registers need the burst bit to read correctly.
        header = addr | READ_SINGLE
        if addr >= 0x30:
            header = addr | READ_BURST
        return self.spi.xfer([header, 0x00])[1]

    def write_burst(self, addr, values):
        self.spi.xfer([addr | WRITE_BURST] + list(values))

    # -- lifecycle ------------------------------------------------------------
    def reset(self):
        """Manual power-on reset sequence (CC1101 datasheet 19.1)."""
        self._strobe(SRES)
        time.sleep(0.01)

    def self_test(self):
        """Return (partnum, version). Version should be 0x14 on a real CC1101."""
        part = self.read_reg(PARTNUM)
        ver = self.read_reg(VERSION)
        return part, ver

    def idle(self):
        self._strobe(SIDLE)

    def flush(self):
        self._strobe(SFRX)
        self._strobe(SFTX)

    # -- configuration --------------------------------------------------------
    def configure_ook(self):
        """Load a 433.92 MHz ASK/OOK, asynchronous-serial configuration.

        Key choices for capture/replay:
          IOCFG0  = 0x0D  -> GDO0 outputs async serial data (raw demod in RX)
          PKTCTRL0= 0x30  -> asynchronous serial mode, no packet handling
          MDMCFG2 = 0x30  -> ASK/OOK, no sync word, no Manchester
        The rest are standard SmartRF values for 433.92 MHz / ~4.8 kBaud.
        """
        self.reset()
        cfg = {
            IOCFG2: 0x0D,    # GDO2 also async data (handy for scoping)
            IOCFG0: 0x0D,    # GDO0 = async serial data out
            FIFOTHR: 0x47,
            PKTCTRL0: 0x30,  # async serial mode
            FSCTRL1: 0x06,
            FREQ2: 0x10,     # 433.92 MHz
            FREQ1: 0xB0,
            FREQ0: 0x71,
            MDMCFG4: 0xF8,   # ~4.8 kBaud data rate / RX filter BW
            MDMCFG3: 0x93,
            MDMCFG2: 0x30,   # ASK/OOK, no sync
            MDMCFG1: 0x22,
            MDMCFG0: 0xF8,
            MCSM0: 0x18,     # auto-calibrate when going from IDLE
            FOCCFG: 0x16,
            AGCCTRL2: 0x03,
            AGCCTRL1: 0x00,
            AGCCTRL0: 0x91,
            FREND1: 0x56,
            FREND0: 0x11,    # OOK: PATABLE index 1 = "on" power
            FSCAL3: 0xE9,
            FSCAL2: 0x2A,
            FSCAL1: 0x00,
            FSCAL0: 0x1F,
            TEST2: 0x81,
            TEST1: 0x35,
            TEST0: 0x09,
        }
        for addr, val in cfg.items():
            self.write_reg(addr, val)

        # PATABLE for OOK: index 0 = off (0x00), index 1 = on (~10 dBm = 0xC0).
        self.write_burst(PATABLE, [0x00, 0xC0])

    def set_frequency(self, mhz):
        """Set carrier frequency. Call after configure_ook() if not 433.92.

        FREQ = freq_MHz * 2^16 / 26  (26 MHz crystal). Common: 315, 433.92.
        """
        freq_word = int(mhz * 65536 / 26.0)
        self.write_reg(FREQ2, (freq_word >> 16) & 0xFF)
        self.write_reg(FREQ1, (freq_word >> 8) & 0xFF)
        self.write_reg(FREQ0, freq_word & 0xFF)

    # -- mode control ---------------------------------------------------------
    def receive_mode(self):
        self.idle()
        self.flush()
        self._strobe(SRX)

    def transmit_mode(self):
        self.idle()
        self.flush()
        self._strobe(STX)

    def close(self):
        self.idle()
        self.spi.close()


if __name__ == "__main__":
    # Smoke test: confirm the Pi can talk to the chip over SPI.
    radio = CC1101()
    radio.reset()
    part, ver = radio.self_test()
    print(f"PARTNUM=0x{part:02X}  VERSION=0x{ver:02X}")
    if ver in (0x14, 0x04):
        print("[+] CC1101 responding over SPI.")
    else:
        print("[!] Unexpected VERSION. Check 3.3V power, SPI enabled, and the")
        print("    MOSI/MISO/SCLK/CSN wiring (pins 19/21/23/24).")
    radio.close()
