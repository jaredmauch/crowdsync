## CrowdSync 433/443 MHz TX Prototype

Python 3 tools for using a Raspberry Pi as a simple 433/443 MHz OOK transmitter to talk to CrowdSync devices that use the CMT2210LC OOK receiver.

This is an **RF/protocol exploration** project. The goal is to generate controlled on‑air signals so we can reverse‑engineer how CrowdSync receivers interpret frames and control their R/G/B LEDs.

### Hardware overview

- **Host**: Raspberry Pi 4 or 5 (40‑pin header, a.k.a. J1 / GPIO40)
- **RF module**: Simple 433/443 MHz OOK ASK transmitter module
- **Receiver side**: CrowdSync devices with CMT2210LC OOK receivers (433.92 MHz, 1–5 kbps OOK, per datasheet)

### Wiring (3.3 V, Raspberry Pi 4/5)

All pin numbers below refer to the **40‑pin J1 header**:

- **RF module VCC** → **3V3 rail**
  - Physical pin **1** (`3V3`) or
  - Physical pin **17** (`3V3`)
- **RF module GND** → any ground pin, e.g. physical pin **6**
- **RF module DATA** → **BCM 17**, which is **physical pin 11**
- **RF module enable (TX enable)** → **BCM 27**, which is **physical pin 13** (driven HIGH only while transmitting)

The transmitter module must accept **3.3 V logic** on its DATA and enable inputs. Power from 3V3 unless you are certain the specific module requires 5 V and is 3V3‑safe on DATA.

### Software components

- `crowdsync_tx.py`
  - Minimal OOK bitstream transmitter using `RPi.GPIO`
  - Configurable:
    - Data GPIO pin (default **BCM 17**, physical pin 11)
    - Enable GPIO pin (default **BCM 27**, physical pin 13); must be HIGH during transmit
    - Bitrate (default **3000 bps**, valid 1–5000 bps)
    - Payload, as one of:
      - Hex (`--hex A5F0FF`)
      - Explicit bit pattern (`--pattern 10101010`)
      - **Pulse-width** (`--pulses 253,759,253,759,...`) – alternating high/low durations in µs
      - **EV1527** (`--ev1527 --ev1527-id 0x12345 --ev1527-code 15`) – for commodity 433 MHz LED remotes
    - Repeat count and inter‑frame gap (in “gap bits” for NRZ)
  - Intended for use with an SDR/logic analyzer while we discover the actual CrowdSync protocol; pulse-width and EV1527 modes let you mirror common 433 MHz LED remote behavior (see **PROTOCOLS.md**).

### Installing dependencies

On the Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3 python3-pip
pip3 install RPi.GPIO
```

### Basic usage

From the project directory (`/home/jared/ai/rf`):

#### Transmit a hex payload

```bash
python3 crowdsync_tx.py \
  --hex A5F0FF \
  --bitrate 3000 \
  --repeat 10 \
  --gap-bits 40
```

#### Transmit a raw bit pattern

```bash
python3 crowdsync_tx.py \
  --pattern 10101010 \
  --bitrate 2000 \
  --repeat 50
```

Command‑line options:

- `--pin` – BCM GPIO number for DATA (default `17`)
- `--enable-pin` – BCM GPIO number for TX enable (default `27`)
- `--bitrate` – bit rate in bps (1–5000, default `3000`); used for NRZ/hex/pattern/scan only
- `--hex` – hex payload to send
- `--pattern` – explicit 0/1 bit pattern
- `--pulses` – pulse-width mode: comma-separated high/low durations in µs
- `--ev1527` – send EV1527-style frame (20-bit ID + 4-bit code); use with `--ev1527-id`, `--ev1527-code`, `--ev1527-repeat`
- `--repeat` – number of times to send the full sequence
- `--gap-bits` – number of zero bits between repeats (NRZ mode only)
- `--msb-first` / `--lsb-first` – bit order within each byte for hex payloads

See **PROTOCOLS.md** for 433 MHz LED protocols (EV1527/PT2262), DMX-style RGB channel layout, and package references (rpi-rf, pilight, etc.).

### Reverse‑engineering workflow (planned)

1. **Record reference traffic** from an existing CrowdSync transmitter using an SDR or logic analyzer.
2. **Measure** carrier frequency, bitrate, framing, and encoding (preamble, sync, payload, checksum, Manchester vs. NRZ, etc.).
3. **Replicate** captured frames using `crowdsync_tx.py` and verify that CrowdSync receivers respond.
4. **Abstract** a `CrowdSyncProtocol` layer (future work) to:
   - Build valid frames from high‑level commands (e.g. set LED color/pattern)
   - Handle retries, IDs/groups, and any checksums

### Protocol reference

**PROTOCOLS.md** documents:

- CMT2210LC constraints and OOK encoding options
- Packages that mirror 433 MHz LED/switch behavior (rpi-rf, sendook, pilight, etc.)
- EV1527/PT2262 pulse-width encoding (common in commodity LED remotes)
- DMX512 RGB channel layout for protocol design

### Status

- Hardware and bit‑level TX scaffolding: **initial prototype complete**
- Pulse-width and EV1527 modes: **added** for mirroring commodity 433 MHz LED remotes
- CrowdSync protocol understanding: **TBD – requires captures and experimentation**

