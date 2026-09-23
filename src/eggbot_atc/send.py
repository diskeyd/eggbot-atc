"""Minimal line-by-line GRBL streamer for debugging. UGS is the normal sender."""

import sys
import time

import serial


def send(port, lines, baud=115200, log=sys.stderr):
    with serial.Serial(port, baud, timeout=10) as ser:
        ser.write(b"\r\n\r\n")
        time.sleep(2)  # GRBL reboots on connect
        ser.reset_input_buffer()
        for i, line in enumerate(lines, 1):
            line = line.split(";")[0].strip()
            if not line:
                continue
            ser.write((line + "\n").encode())
            while True:
                reply = ser.readline().decode(errors="replace").strip()
                if reply.startswith("ok"):
                    break
                if reply.startswith(("error", "ALARM")):
                    raise RuntimeError(f"line {i} '{line}': {reply}")
                if not reply:
                    raise RuntimeError(f"line {i} '{line}': no reply (timeout)")
            print(f"{i}/{len(lines)} {line}", file=log)
