# 433 MHz LED control – protocols and integration notes

Reference for integrating RGB/RGBW-style control with the CrowdSync scanner and for understanding how 433 MHz LED devices and DMX-style layouts relate.

## CMT2210LC receiver constraints (your receivers)

From the CMT2210LC datasheet:

- **Modulation**: OOK (On-Off Keying)
- **Frequency**: 315 MHz or **433.92 MHz** (crystal-dependent)
- **Data rate**: **1.0–5.0 kbps**
- **Receiver bandwidth**: 330 kHz @ 433.92 MHz

The chip outputs **demodulated data on DOUT**; it does not define a protocol. Preamble, sync, frame format, and encoding (NRZ vs pulse-width) are determined by the transmitter and the MCU/firmware on the receiver. So CrowdSync may use either:

- **Fixed bit-rate NRZ** (what `crowdsync_tx.py` uses now), or  
- **Pulse-width encoding** (like many commodity 433 MHz remotes).

Both are valid OOK within the 1–5 kbps and bandwidth limits.

---

## Packages that mirror 433 MHz LED / switch behavior

These can control or emulate 433 MHz devices (switches, dimmers, RGB remotes) and are useful to compare with CrowdSync or to drive commodity LED hardware on the same band.

| Package | Description | Relevance |
|--------|-------------|-----------|
| **[rpi-rf](https://github.com/milaq/rpi-rf)** | Send/receive 433 MHz on Raspberry Pi GPIO; **configurable pulse lengths** and protocols. | Fits Pi + OOK module; you can define custom pulse widths to match a protocol or try EV1527-style timing. |
| **[sendook](https://github.com/faragoa/sendook)** | Python ASK/OOK sender (platform-agnostic). | Good if you move off Pi or want a minimal OOK API. |
| **[raspicode](https://github.com/latchdevel/raspicode)** | High-precision 315/433 MHz TX for Raspberry Pi. | Alternative for strict timing. |
| **[pilight](https://github.com/pilight/pilight)** | C daemon with many 433.92 MHz protocols (EV1527, Nexa, KlikAanKlikUit, dimmers, etc.). | **Protocol reference** and pulse timings; [manual](https://manual.pilight.org/protocols/433.92/index.html) documents pulse sequences. |
| **[micropython-radio-control](https://github.com/sebromero/micropython-radio-control)** | MicroPython 433/315 MHz; based on rc-switch; supports EV1527. | Useful if you later use a MicroPython MCU for TX. |

Integration with this repo:

- **rpi-rf** and **sendook** use **pulse-width encoding** (different timing than our NRZ mode). To “mirror” commodity LED remotes, add a pulse-width mode (see below) or call rpi-rf from a wrapper script that translates high-level “set color” into the protocol it expects.
- **pilight** is a good reference for exact pulse timings (e.g. EV1527 253 µs / 759 µs) when implementing or reverse-engineering.

---

## EV1527 / PT2262 (common 433 MHz remote encoding)

Many cheap 433 MHz LED strips and remotes use **EV1527** (or PT2262) encoding: **pulse-width encoding**, not fixed-period NRZ.

### EV1527 timing (typical)

| Symbol   | High (µs) | Low (µs) | Total (µs) |
|---------|-----------|----------|------------|
| Logic 0 | 275       | 275      | 550        |
| Logic 1 | 275       | 1225     | 1500       |
| Sync    | 275       | 2675     | 2950       |

- **0** = short high + short low  
- **1** = short high + long low  
- **Sync** = short high + very long low (frame boundary)

### Frame structure (EV1527)

- Preamble (often zeros) then a **sync** pulse.
- **24 bits**: 20-bit ID + 4-bit data (e.g. button/channel).
- Frame often sent **multiple times** per keypress (e.g. 4×) for reliability.

So effective “bit rate” is variable; the CMT2210LC can still receive this as long as the pulse widths are within its bandwidth and timing (1–5 kbps is a guideline for *average* symbol rate).

### Pilight EV1527 pulse example

Pilight documents EV1527 as a sequence of **high/low pulse lengths in µs**. Example (contact protocol): pulses like `253 759 759 253 ...` with a long footer (~8602 µs). So 253 µs / 759 µs are the two symbol lengths; the long gap is sync/footer.

If CrowdSync uses similar encoding, you’d need to send **pulses** (high/low durations) instead of NRZ bits. The scanner can be extended with a pulse-width mode (see `crowdsync_tx.py` `--pulses` / EV1527 helpers) to try these timings.

---

## DMX512 and RGB channel layout

DMX512 is **wired** (RS-485, 250 kbps), not 433 MHz, but RGB fixtures and “DMX mixboards” use a standard **channel layout** that is useful when designing or reverse-engineering a protocol.

### DMX packet (brief)

- **Break**: ≥88 µs low  
- **Mark After Break (MAB)**: ≥8 µs high  
- **Data**: up to 512 channels, 1 byte (0–255) per channel, 8N2 serial.

### RGB fixture mapping

- **3 channels per fixture**: Channel N = Red, N+1 = Green, N+2 = Blue (each 0–255).
- **RGBW**: often 4 channels (R, G, B, W).

So when you reverse-engineer CrowdSync:

- Look for **3 or 4 consecutive fields** (bytes or nibbles) that behave like R, G, B [, W].
- A “DMX-style” layout would be: `[preamble/sync][addr?][R][G][B]` or `[R][G][B][W]` in some order. That doesn’t mean CrowdSync speaks DMX on the wire—only that the **logical** format (separate R/G/B channels, 0–255 or similar) can mirror DMX for integration (e.g. map “DMX channel 1–3” to “CrowdSync R,G,B” in software).

---

## Integrating into the scanner

1. **Keep current NRZ mode** for CrowdSync reverse-engineering (hex/pattern/scan).
2. **Add pulse-width encoding** so you can:
   - Send **EV1527-style** frames (20-bit ID + 4-bit data) to try commodity 433 MHz LED remotes.
   - Send **custom pulse sequences** (e.g. from pilight or logic-analyzer captures) to test receivers.
3. **Protocol format (future)**  
   Once CrowdSync frame format is known, define a small “channel” abstraction (e.g. 3–4 bytes for R, G, B [, W]) so you can map from a DMX-like or high-level “set color” API into the actual OOK frame.

`crowdsync_tx.py` now supports a **pulse-width mode** (`--pulses` and optional EV1527 helpers) so you can mirror EV1527-style behavior and compare with both CrowdSync and commodity LED receivers.
