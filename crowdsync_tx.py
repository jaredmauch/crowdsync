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
  - Send DMX512-style channel data (R,G,B etc.) over 433 MHz OOK (--dmx, --dmx-scan)

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

    def send_pulses(self, pulse_us: List[int], repeat: int = 1) -> None:
        """
        Send a sequence of high/low pulse durations (OOK pulse-width encoding).

        pulse_us: alternating [high1, low1, high2, low2, ...] in microseconds.
        repeat: send the full sequence this many times.
        Enable pin is driven HIGH for the full transmission.
        """
        if not pulse_us:
            return
        GPIO.output(self.enable_pin, GPIO.HIGH)
        try:
            for _ in range(max(1, repeat)):
                level = GPIO.HIGH
                for duration_us in pulse_us:
                    if duration_us <= 0:
                        continue
                    GPIO.output(self.gpio_pin, level)
                    time.sleep(duration_us / 1_000_000.0)
                    level = GPIO.LOW if level == GPIO.HIGH else GPIO.HIGH
        finally:
            GPIO.output(self.gpio_pin, GPIO.LOW)
            GPIO.output(self.enable_pin, GPIO.LOW)


# EV1527-style pulse-width encoding (common in 433 MHz LED remotes)
# Timing in µs: short high = 275; low for 0 = 275, for 1 = 1225, sync = 2675
EV1527_HIGH_US = 275
EV1527_LOW_0_US = 275
EV1527_LOW_1_US = 1225
EV1527_SYNC_LOW_US = 2675


def ev1527_pulses(id_20bit: int, data_4bit: int, repeat: int = 4) -> List[int]:
    """
    Build EV1527 pulse sequence (high/low µs) for 20-bit ID + 4-bit data.
    One frame = sync + 24 bits MSB first; repeated `repeat` times.
    """
    # 24 bits: id_20bit (20 msb) then data_4bit (4 lsb)
    bits: List[int] = []
    for i in range(19, -1, -1):
        bits.append((id_20bit >> i) & 1)
    for i in range(3, -1, -1):
        bits.append((data_4bit >> i) & 1)
    pulse_list: List[int] = []
    for _ in range(repeat):
        # Sync
        pulse_list.append(EV1527_HIGH_US)
        pulse_list.append(EV1527_SYNC_LOW_US)
        for b in bits:
            pulse_list.append(EV1527_HIGH_US)
            pulse_list.append(EV1527_LOW_1_US if b else EV1527_LOW_0_US)
    return pulse_list


def bytes_to_bits(byte_list: List[int], msb_first: bool = True) -> List[int]:
    """Convert a list of byte values (0–255) to a list of bits."""
    bits: List[int] = []
    for byte_val in byte_list:
        for bit_index in range(8):
            shift = 7 - bit_index if msb_first else bit_index
            bits.append((byte_val >> shift) & 0x01)
    return bits


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
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan data space: send 4-byte frames (prefix 0x03 or 0x04 + 24-bit counter 0x000000–0xffffff), print progress every 0x100.",
    )
    parser.add_argument(
        "--scan-prefix",
        type=int,
        choices=(3, 4),
        default=3,
        help="First byte of each 4-byte frame in scan mode (default: 3).",
    )
    parser.add_argument(
        "--pulses",
        type=str,
        default=None,
        metavar="US",
        help="Pulse-width mode: comma-separated high/low durations in µs (e.g. '253,759,253,759').",
    )
    parser.add_argument(
        "--ev1527",
        action="store_true",
        help="Send EV1527-style frame (20-bit ID + 4-bit data) for commodity 433 MHz LED remotes.",
    )
    parser.add_argument(
        "--ev1527-id",
        type=lambda x: int(x, 0),
        default=0,
        help="EV1527 20-bit ID (decimal or 0xhex). Default 0.",
    )
    parser.add_argument(
        "--ev1527-code",
        type=lambda x: int(x, 0),
        default=0,
        help="EV1527 4-bit data/code (0–15). Default 0.",
    )
    parser.add_argument(
        "--ev1527-repeat",
        type=int,
        default=4,
        help="Number of EV1527 frame repeats (default: 4).",
    )
    parser.add_argument(
        "--ev1527-scan",
        action="store_true",
        help="EV1527 scan mode: cycle through all 16 codes (and optional ID range) quickly.",
    )
    parser.add_argument(
        "--ev1527-id-end",
        type=lambda x: int(x, 0),
        default=None,
        help="EV1527 scan end ID (inclusive). If set with --ev1527-scan, scan IDs from --ev1527-id to this; otherwise scan only --ev1527-id.",
    )
    parser.add_argument(
        "--ev1527-fast",
        action="store_true",
        help="Use minimal repeats in EV1527 scan (1 frame repeat, 1 transmission repeat) for speed.",
    )
    # DMX512-style channel data over 433 MHz OOK
    parser.add_argument(
        "--dmx",
        dest="dmx_channels",
        type=str,
        default=None,
        metavar="CHANNELS",
        help="DMX512-style channel values over 433 MHz: comma-separated 0–255 (e.g. '255,0,128' for R,G,B).",
    )
    parser.add_argument(
        "--dmx-start-code",
        action="store_true",
        default=True,
        dest="dmx_start_code",
        help="Prepend DMX null start code 0x00 to channel payload (default: True).",
    )
    parser.add_argument(
        "--no-dmx-start-code",
        action="store_false",
        dest="dmx_start_code",
        help="Omit DMX start code; send only channel bytes.",
    )
    parser.add_argument(
        "--dmx-scan",
        action="store_true",
        help="DMX scan over 433 MHz: sweep one channel 0–255, others zero.",
    )
    parser.add_argument(
        "--dmx-start-addr",
        type=int,
        default=1,
        help="DMX start address (1-based) for scan; first channel index (default: 1).",
    )
    parser.add_argument(
        "--dmx-num-channels",
        type=int,
        default=3,
        help="Number of DMX channels (e.g. 3 for RGB, 4 for RGBW) in scan (default: 3).",
    )
    parser.add_argument(
        "--dmx-scan-channel",
        type=int,
        default=0,
        help="Which channel index (0-based) to sweep in DMX scan (default: 0 = first).",
    )

    args = parser.parse_args(argv)

    if args.ev1527_id_end is not None and not args.ev1527_scan:
        parser.error("--ev1527-id-end only applies with --ev1527-scan")
    if args.ev1527_fast and not args.ev1527_scan:
        parser.error("--ev1527-fast only applies with --ev1527-scan")
    if args.dmx_scan and args.dmx_channels is not None:
        parser.error("use either --dmx or --dmx-scan, not both")
    if args.dmx_channels is not None and not args.dmx_scan:
        pass  # --dmx without --dmx-scan
    if args.dmx_scan:
        if not (1 <= args.dmx_start_addr <= 512):
            parser.error("--dmx-start-addr must be 1–512")
        if not (1 <= args.dmx_num_channels <= 512):
            parser.error("--dmx-num-channels must be 1–512")
        if not (0 <= args.dmx_scan_channel < args.dmx_num_channels):
            parser.error(
                "--dmx-scan-channel must be 0 to (dmx-num-channels - 1)"
            )

    mode_count = sum(
        [
            args.scan,
            args.hex_payload is not None,
            args.pattern is not None,
            args.pulses is not None,
            args.ev1527,
            args.ev1527_scan,
            args.dmx_channels is not None,
            args.dmx_scan,
        ]
    )
    if mode_count == 0:
        parser.error(
            "you must provide one of: --hex, --pattern, --scan, --pulses, "
            "--ev1527, --ev1527-scan, --dmx, --dmx-scan"
        )
    if mode_count > 1:
        parser.error(
            "use only one of: --hex, --pattern, --scan, --pulses, "
            "--ev1527, --ev1527-scan, --dmx, --dmx-scan"
        )
    if args.scan and (args.hex_payload is not None or args.pattern is not None):
        parser.error("do not use --hex or --pattern with --scan")
    if args.hex_payload is not None and args.pattern is not None:
        parser.error("use either --hex or --pattern, not both")

    if not (1 <= args.bitrate <= 5000):
        parser.error("bitrate must be between 1 and 5000 bps")
    if args.ev1527:
        if not (0 <= args.ev1527_id <= 0xFFFFF):
            parser.error("--ev1527-id must be 0–1048575 (20-bit)")
        if not (0 <= args.ev1527_code <= 15):
            parser.error("--ev1527-code must be 0–15")
        if args.ev1527_repeat < 1:
            parser.error("--ev1527-repeat must be >= 1")
    if args.ev1527_scan:
        if not (0 <= args.ev1527_id <= 0xFFFFF):
            parser.error("--ev1527-id must be 0–1048575 (20-bit)")
        if args.ev1527_id_end is not None:
            if not (0 <= args.ev1527_id_end <= 0xFFFFF):
                parser.error("--ev1527-id-end must be 0–1048575 (20-bit)")
            if args.ev1527_id_end < args.ev1527_id:
                parser.error("--ev1527-id-end must be >= --ev1527-id")

    return args


def build_bit_sequence(args: argparse.Namespace) -> List[int]:
    if args.hex_payload is not None:
        return hex_to_bits(args.hex_payload, msb_first=args.msb_first)

    assert args.pattern is not None
    pattern = args.pattern.strip().replace(" ", "")
    if any(c not in ("0", "1") for c in pattern):
        raise ValueError("pattern must contain only '0' and '1' characters")
    return [1 if c == "1" else 0 for c in pattern]


def parse_pulses_arg(pulses_str: str) -> List[int]:
    """Parse comma-separated list of pulse durations in µs."""
    return [int(s.strip(), 10) for s in pulses_str.split(",") if s.strip()]


# DMX512-style channel data over 433 MHz OOK (channel bytes sent as RF payload, not wired DMX)
def parse_dmx_channels(s: str) -> List[int]:
    """Parse comma- or space-separated channel values (0–255)."""
    parts = s.replace(",", " ").split()
    out: List[int] = []
    for p in parts:
        v = int(p.strip(), 0)
        if not (0 <= v <= 255):
            raise ValueError(f"DMX channel value must be 0–255, got {v}")
        out.append(v)
    return out


def build_dmx_payload(
    channel_values: List[int],
    start_code: bool = True,
) -> List[int]:
    """
    Build DMX512-style byte payload for RF: optional null start code (0x00) + channel bytes.
    Returns list of byte values (0–255) to send over OOK.
    """
    payload: List[int] = []
    if start_code:
        payload.append(0x00)
    payload.extend(channel_values)
    return payload


def run_dmx_scan(args: argparse.Namespace, tx: OokTransmitter) -> None:
    """Scan one DMX channel 0–255 over 433 MHz; other channels zero."""
    start_addr = args.dmx_start_addr  # 1-based first channel index
    num_channels = args.dmx_num_channels
    scan_channel = args.dmx_scan_channel  # 0-based index of channel to sweep
    if scan_channel >= num_channels:
        raise ValueError("dmx-scan-channel must be < dmx-num-channels")
    progress_interval = 16

    for value in range(256):
        channels = [0] * num_channels
        channels[scan_channel] = value
        payload = build_dmx_payload(channels, start_code=args.dmx_start_code)
        bits = bytes_to_bits(payload, msb_first=args.msb_first)
        tx.send_bits(bits, repeat=args.repeat, gap_bits=args.gap_bits)
        if value % progress_interval == 0 or value == 255:
            print(f"DMX scan channel {start_addr + scan_channel}={value}/255")

    print("DMX scan complete.")


def run_scan(args: argparse.Namespace, tx: OokTransmitter) -> None:
    """Send 4-byte frames: prefix (0x03 or 0x04) then 24-bit value 0x000000–0xffffff; print every 0x100."""
    prefix = args.scan_prefix
    progress_interval = 0x100

    for value in range(0x1000000):
        frame = [
            prefix,
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ]
        bits = bytes_to_bits(frame, msb_first=args.msb_first)
        tx.send_bits(bits, repeat=args.repeat, gap_bits=args.gap_bits)

        if value % progress_interval == 0:
            print(f"Scan 0x{value:06X} / 0xFFFFFF")

    print("Scan complete.")


def run_ev1527_scan(args: argparse.Namespace, tx: OokTransmitter) -> None:
    """Cycle through EV1527 (ID, code) combinations: 16 codes per ID, optional ID range."""
    id_start = args.ev1527_id
    id_end = args.ev1527_id if args.ev1527_id_end is None else args.ev1527_id_end
    frame_repeat = 1 if args.ev1527_fast else 4
    tx_repeat = 1 if args.ev1527_fast else max(1, args.repeat)
    total = (id_end - id_start + 1) * 16
    progress_interval = 16  # print every 16 codes (one full ID)

    sent = 0
    for dev_id in range(id_start, id_end + 1):
        for code in range(16):
            pulse_us = ev1527_pulses(dev_id, code, repeat=frame_repeat)
            tx.send_pulses(pulse_us, repeat=tx_repeat)
            sent += 1
        if (sent % progress_interval) == 0 or sent == total:
            print(f"EV1527 scan {sent}/{total} (ID=0x{dev_id:05X} code=0–15)")

    print("EV1527 scan complete.")


def main(argv: List[str]) -> int:
    try:
        args = parse_args(argv)
    except SystemExit:
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    bits: List[int] = []
    if args.hex_payload is not None or args.pattern is not None:
        try:
            bits = build_bit_sequence(args)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not bits:
            print("No bits to transmit (empty payload).", file=sys.stderr)
            return 1

    pulse_us: List[int] = []
    if args.pulses is not None:
        try:
            pulse_us = parse_pulses_arg(args.pulses)
        except Exception as exc:
            print(f"Error parsing --pulses: {exc}", file=sys.stderr)
            return 1
        if not pulse_us:
            print("No pulses to transmit (empty --pulses).", file=sys.stderr)
            return 1
    if args.ev1527:
        pulse_us = ev1527_pulses(
            args.ev1527_id,
            args.ev1527_code,
            repeat=args.ev1527_repeat,
        )

    dmx_bits: List[int] = []
    if args.dmx_channels is not None:
        try:
            ch = parse_dmx_channels(args.dmx_channels)
            if not ch:
                raise ValueError("at least one DMX channel value required")
            payload = build_dmx_payload(ch, start_code=args.dmx_start_code)
            dmx_bits = bytes_to_bits(payload, msb_first=args.msb_first)
        except Exception as exc:
            print(f"Error parsing --dmx: {exc}", file=sys.stderr)
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

    try:
        if args.dmx_scan:
            print(
                f"DMX scan over 433 MHz: start-addr={args.dmx_start_addr}, "
                f"channels={args.dmx_num_channels}, sweep channel index {args.dmx_scan_channel} (0–255), "
                f"bitrate={args.bitrate} bps (data=GPIO {args.pin})."
            )
            run_dmx_scan(args, tx)
        elif args.ev1527_scan:
            id_end = args.ev1527_id if args.ev1527_id_end is None else args.ev1527_id_end
            total = (id_end - args.ev1527_id + 1) * 16
            print(
                f"EV1527 scan: ID 0x{args.ev1527_id:05X}–0x{id_end:05X}, "
                f"16 codes each ({total} combinations), "
                f"fast={args.ev1527_fast} (data=GPIO {args.pin})."
            )
            run_ev1527_scan(args, tx)
        elif args.scan:
            print(
                f"Scan mode: 4-byte frames (prefix=0x{args.scan_prefix:02X}, "
                f"counter 0x000000–0xFFFFFF) at {args.bitrate} bps, "
                f"repeat={args.repeat}, gap_bits={args.gap_bits}. Progress every 0x100."
            )
            run_scan(args, tx)
        elif args.dmx_channels is not None:
            print(
                f"Transmitting DMX-style payload ({len(dmx_bits)} bits) over 433 MHz "
                f"at {args.bitrate} bps (data=GPIO {args.pin}), "
                f"repeat={args.repeat}, gap_bits={args.gap_bits}..."
            )
            tx.send_bits(dmx_bits, repeat=args.repeat, gap_bits=args.gap_bits)
            print("Done.")
        elif args.ev1527 or args.pulses is not None:
            n = len(pulse_us)
            print(
                f"Transmitting {n} pulse durations (pulse-width OOK) "
                f"(data=GPIO {args.pin}, enable=GPIO {args.enable_pin}), "
                f"repeat={args.repeat}..."
            )
            tx.send_pulses(pulse_us, repeat=args.repeat)
            print("Done.")
        else:
            print(
                f"Transmitting {len(bits)} bits at {args.bitrate} bps "
                f"(data=GPIO {args.pin}, enable=GPIO {args.enable_pin}), "
                f"repeat={args.repeat}, gap_bits={args.gap_bits}..."
            )
            tx.send_bits(bits, repeat=args.repeat, gap_bits=args.gap_bits)
            print("Done.")
    finally:
        tx.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

