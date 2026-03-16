#!/usr/bin/env python3

"""
crowdsync_tx.py

Basic 433/443 MHz OOK transmitter driver for Raspberry Pi intended to talk to
CrowdSync receivers that use the CMT2210LC OOK receiver.

This module does NOT yet implement the CrowdSync-specific protocol; instead it
provides a flexible bitstream transmitter with:
  - Configurable GPIO pin
  - Configurable bit rate (1–5 kbps as per CMT2210LC spec)
  - Simple CLI for sending raw bit patterns / hex payloads

You can use this to:
  - Generate test patterns (preambles, repeating codes)
  - Capture the corresponding waveforms on a logic analyzer / SDR
  - Reverse engineer the actual CrowdSync frame format and LED commands

Default wiring assumptions (Raspberry Pi 4/5, 40-pin header):
  - DATA from the 433/443 MHz OOK TX module → BCM 17 (physical pin 11)
  - Enable (TX enable) → BCM 27 (physical pin 13); driven HIGH while transmitting
  - Module VCC → 3.3 V (3V3 pin 1 of GPIO header)
  - Module GND → any ground pin (e.g. physical pin 6)
"""

import argparse
import sys
import time
from typing import Iterable, List

try:
    import RPi.GPIO as GPIO  # type: ignore[import-untyped]
except ImportError as exc:  # pragma: no cover - for non-RPi development
    GPIO = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


DEFAULT_GPIO_PIN = 17  # BCM numbering (data)
DEFAULT_ENABLE_PIN = 27  # BCM numbering; must be HIGH during transmit
DEFAULT_BITRATE = 3000  # bits per second (within 1–5 kbps CMT2210LC range)


class GpioUnavailableError(RuntimeError):
    pass


def require_gpio() -> None:
    """Ensure RPi.GPIO is available."""
    if GPIO is None:
        raise GpioUnavailableError(
            "RPi.GPIO is not available. "
            "Install it on a Raspberry Pi or run this on the Pi itself.\n"
            f"Original import error: {_IMPORT_ERROR}"
        )


class OokTransmitter:
    """
    Simple OOK (On-Off Keying) transmitter using a digital GPIO pin.

    Assumptions:
      - External RF module handles RF carrier generation at ~433–443 MHz.
      - DATA pin keys the carrier on/off; enable pin must be HIGH during transmit.
      - Logic HIGH on DATA = carrier ON, logic LOW = carrier OFF.
      - NRZ coding: each bit occupies a full bit period at fixed level.
    """

    def __init__(
        self,
        gpio_pin: int = DEFAULT_GPIO_PIN,
        enable_pin: int = DEFAULT_ENABLE_PIN,
        bitrate: int = DEFAULT_BITRATE,
    ):
        require_gpio()
        if bitrate <= 0:
            raise ValueError("bitrate must be > 0")

        self.gpio_pin = gpio_pin
        self.enable_pin = enable_pin
        self.bitrate = bitrate
        self.bit_period = 1.0 / float(bitrate)

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.gpio_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.enable_pin, GPIO.OUT, initial=GPIO.LOW)

    def cleanup(self) -> None:
        """Release GPIO resources."""
        GPIO.output(self.gpio_pin, GPIO.LOW)
        GPIO.output(self.enable_pin, GPIO.LOW)
        GPIO.cleanup(self.gpio_pin)
        GPIO.cleanup(self.enable_pin)

    def send_bit(self, bit: int) -> None:
        """Send a single bit (0 or 1) using OOK."""
        level = GPIO.HIGH if bit else GPIO.LOW
        GPIO.output(self.gpio_pin, level)
        time.sleep(self.bit_period)

    def send_bits(self, bits: Iterable[int], repeat: int = 1, gap_bits: int = 0) -> None:
        """
        Send a sequence of bits, optionally repeating with a gap.
        Enable pin is driven HIGH for the full transmission and LOW when done.

        - bits: iterable of 0/1 integers
        - repeat: send the full sequence this many times
        - gap_bits: number of '0' bits inserted between repeats
        """
        bit_seq: List[int] = [1 if b else 0 for b in bits]
        if not bit_seq:
            return

        GPIO.output(self.enable_pin, GPIO.HIGH)
        try:
            for _ in range(max(1, repeat)):
                for b in bit_seq:
                    self.send_bit(b)
                for _ in range(max(0, gap_bits)):
                    self.send_bit(0)
        finally:
            GPIO.output(self.gpio_pin, GPIO.LOW)
            GPIO.output(self.enable_pin, GPIO.LOW)



def hex_to_bits(hex_string: str, msb_first: bool = True) -> List[int]:
    """
    Convert a hex string (e.g. 'A5F0') to a list of bits.

    - msb_first=True: bit order per byte is MSB→LSB (usual on-air encoding)
    """
    hex_string = hex_string.strip().replace(" ", "").replace("0x", "").replace("0X", "")
    if len(hex_string) == 0:
        return []
    if len(hex_string) % 2 == 1:
        # pad leading zero for odd-length strings
        hex_string = "0" + hex_string

    bits: List[int] = []
    for i in range(0, len(hex_string), 2):
        byte_val = int(hex_string[i : i + 2], 16)
        for bit_index in range(8):
            if msb_first:
                shift = 7 - bit_index
            else:
                shift = bit_index
            bits.append((byte_val >> shift) & 0x01)
    return bits


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transmit raw OOK bitstreams for CrowdSync reverse-engineering."
        )
    )
    parser.add_argument(
        "--pin",
        type=int,
        default=DEFAULT_GPIO_PIN,
        help=f"BCM GPIO pin for RF module data input (default: {DEFAULT_GPIO_PIN})",
    )
    parser.add_argument(
        "--enable-pin",
        type=int,
        default=DEFAULT_ENABLE_PIN,
        help=f"BCM GPIO pin for TX enable, driven HIGH while transmitting (default: {DEFAULT_ENABLE_PIN})",
    )
    parser.add_argument(
        "--bitrate",
        type=int,
        default=DEFAULT_BITRATE,
        help=f"Bit rate in bits per second (1–5000, default: {DEFAULT_BITRATE})",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of times to repeat the full sequence (default: 1)",
    )
    parser.add_argument(
        "--gap-bits",
        type=int,
        default=0,
        help="Number of zero bits between repeats (default: 0)",
    )
    parser.add_argument(
        "--hex",
        dest="hex_payload",
        type=str,
        default=None,
        help="Hex payload to transmit (e.g. 'A5F0FF').",
    )
    parser.add_argument(
        "--pattern",
        dest="pattern",
        type=str,
        default=None,
        help="Explicit 0/1 bit pattern to transmit (e.g. '10101010').",
    )
    parser.add_argument(
        "--msb-first",
        dest="msb_first",
        action="store_true",
        default=True,
        help="Interpret hex payload bits MSB→LSB within each byte (default).",
    )
    parser.add_argument(
        "--lsb-first",
        dest="msb_first",
        action="store_false",
        help="Interpret hex payload bits LSB→MSB within each byte.",
    )

    args = parser.parse_args(argv)

    if args.hex_payload is None and args.pattern is None:
        parser.error("you must provide either --hex or --pattern")

    if args.hex_payload is not None and args.pattern is not None:
        parser.error("use either --hex or --pattern, not both")

    if not (1 <= args.bitrate <= 5000):
        parser.error("bitrate must be between 1 and 5000 bps")

    return args


def build_bit_sequence(args: argparse.Namespace) -> List[int]:
    if args.hex_payload is not None:
        return hex_to_bits(args.hex_payload, msb_first=args.msb_first)

    assert args.pattern is not None
    pattern = args.pattern.strip().replace(" ", "")
    if any(c not in ("0", "1") for c in pattern):
        raise ValueError("pattern must contain only '0' and '1' characters")
    return [1 if c == "1" else 0 for c in pattern]


def main(argv: List[str]) -> int:
    try:
        args = parse_args(argv)
        bits = build_bit_sequence(args)
    except SystemExit:
        # argparse already printed an error
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not bits:
        print("No bits to transmit (empty payload).", file=sys.stderr)
        return 1

    try:
        tx = OokTransmitter(
            gpio_pin=args.pin,
            enable_pin=args.enable_pin,
            bitrate=args.bitrate,
        )
    except GpioUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"Failed to initialize transmitter: {exc}", file=sys.stderr)
        return 1

    print(
        f"Transmitting {len(bits)} bits at {args.bitrate} bps "
        f"(data=GPIO {args.pin}, enable=GPIO {args.enable_pin}), "
        f"repeat={args.repeat}, gap_bits={args.gap_bits}..."
    )
    try:
        tx.send_bits(bits, repeat=args.repeat, gap_bits=args.gap_bits)
    finally:
        tx.cleanup()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

