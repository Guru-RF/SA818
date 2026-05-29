#!/usr/bin/env python3
#
# BSD 2-Clause License
#
# Original Copyright (c) 2022-2023 Fred W6BSD
# SA818 / SA818Pro backward-compatible update
#

__doc__ = """sa818 / sa818pro programmer"""

import argparse
import logging
import os
import re
import sys
import textwrap
import time

import serial

logging.basicConfig(format='%(name)s: %(levelname)s: %(message)s',
                    level=logging.INFO)
logger = logging.getLogger('SA818')


CTCSS = (
    "0.0", "67.0", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4", "88.5",
    "91.5", "94.8", "97.4", "100.0", "103.5", "107.2", "110.9", "114.8", "118.8",
    "123.0", "127.3", "131.8", "136.5", "141.3", "146.2", "151.4", "156.7",
    "162.2", "167.9", "173.8", "179.9", "186.2", "192.8", "203.5", "210.7",
    "218.1", "225.7", "233.6", "241.8", "250.3"
)

DCS_CODES = [
    "023", "025", "026", "031", "032", "036", "043", "047", "051", "053", "054",
    "065", "071", "072", "073", "074", "114", "115", "116", "125", "131", "132",
    "134", "143", "152", "155", "156", "162", "165", "172", "174", "205", "223",
    "226", "243", "244", "245", "251", "261", "263", "265", "271", "306", "311",
    "315", "331", "343", "346", "351", "364", "365", "371", "411", "412", "413",
    "423", "431", "432", "445", "464", "465", "466", "503", "506", "516", "532",
    "546", "565", "606", "612", "624", "627", "631", "632", "654", "662", "664",
    "703", "712", "723", "731", "732", "734", "743", "754"
]

BAUD_RATES = [300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
DEFAULT_BAUDRATE = 9600


class SA818:
    EOL = "\r\n"

    INIT = "AT+DMOCONNECT"
    SETGRP = "AT+DMOSETGROUP"
    READGRP = "AT+DMOREADGROUP"
    FILTER = "AT+SETFILTER"
    VOLUME = "AT+DMOSETVOLUME"
    TAIL = "AT+SETTAIL"
    VERSION = "AT+VERSION"
    RSSI_PRO = "RSSI?"
    RSSI_LEGACY = "AT+RSSI?"
    TXOMES = "AT+TXOMES"

    PORTS = ('/dev/serial0', '/dev/ttyUSB0')
    READ_TIMEOUT = 1.0
    COMMAND_TIMEOUT = 3.0

    def __init__(self, port=None, baud=DEFAULT_BAUDRATE, model="auto"):
        self.serial = None
        self.model = model.lower()
        self.firmware_name = None
        self.firmware_version = None
        if self.model not in ("auto", "sa818", "sa818pro"):
            raise ValueError("model must be auto, sa818, or sa818pro")

        ports = [port] if port else self.PORTS

        for _port in ports:
            try:
                self.serial = serial.Serial(
                    port=_port,
                    baudrate=baud,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    bytesize=serial.EIGHTBITS,
                    timeout=self.READ_TIMEOUT,
                )
                logger.debug(self.serial)
                break
            except serial.SerialException as err:
                logger.debug(err)

        if not isinstance(self.serial, serial.Serial):
            raise IOError('Error opening the serial port')

        self.flush()

        # SA818Pro documentation recommends trying the handshake up to 3 times
        # before deciding the module needs a restart.
        reply = None
        for _ in range(3):
            reply = self.command(self.INIT, expect=("+DMOCONNECT:",), timeout=1.5)
            if reply == "+DMOCONNECT:0":
                break
            time.sleep(0.2)

        if reply != "+DMOCONNECT:0":
            raise SystemError(f'Connection error, got: {reply!r}') from None

        # Keep the command line backward compatible: users do not need to pass
        # --model.  We detect SA818Pro internally from AT+VERSION, e.g.
        # +VERSION:SA818PRO_V1.0.
        self.detect_model()

    def close(self):
        if self.serial:
            self.serial.close()

    def flush(self):
        try:
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
        except serial.SerialException as err:
            logger.debug("flush failed: %s", err)

    def send(self, data):
        logger.debug('Sending: %s', data)
        raw = bytes(data + self.EOL, 'ascii')
        try:
            self.serial.write(raw)
        except serial.SerialException as err:
            logger.error(err)

    def readline(self):
        try:
            line = self.serial.readline()
        except serial.SerialException as err:
            logger.warning(err)
            return None

        if not line:
            return None

        try:
            text = line.decode('ascii', errors='strict')
        except UnicodeDecodeError:
            logger.debug(line)
            logger.error('Character decode error: check your baudrate')
            return None

        return text.rstrip("\r\n")

    def command(self, data, expect=None, timeout=None, clear=True):
        """Send a command and return the first useful reply.

        Some modules echo commands or emit asynchronous lines, so this waits
        until an expected prefix is seen, or returns the first non-echo line.
        """
        if timeout is None:
            timeout = self.COMMAND_TIMEOUT

        if clear:
            self.flush()

        self.send(data)
        deadline = time.monotonic() + timeout
        first_non_echo = None

        while time.monotonic() < deadline:
            reply = self.readline()
            if reply is None or reply == "":
                continue

            logger.debug("Received: %s", reply)

            # Ignore command echo, if present.
            if reply == data:
                continue

            if expect:
                if any(reply.startswith(prefix) for prefix in expect):
                    return reply
            elif first_non_echo is None:
                first_non_echo = reply
                return first_non_echo

            if first_non_echo is None:
                first_non_echo = reply

        return first_non_echo

    def detect_model(self):
        """Detect SA818 vs SA818Pro without changing the CLI."""
        detected = self.version(log=False)
        if detected:
            logger.debug("Detected module model: %s", self.model)
        else:
            logger.debug("Could not auto-detect module model; using: %s", self.model)
        return self.model

    def version(self, log=True):
        reply = self.command(self.VERSION, expect=("+VERSION:", "++VERSION:"), timeout=1.5)
        if not reply:
            if log:
                logger.error('No version response')
            return None

        # Datasheets show +VERSION:SA818PRO_V1.0; some documents contain a typo:
        # ++VERSION:SA818PRO_V1.0. Accept both.
        match = re.match(r'^\+{1,2}VERSION:(.+)$', reply)
        if not match:
            if log:
                logger.error('Unable to decode version response: %s', reply)
            return None

        value = match.group(1).strip()
        parts = re.split(r'[_\s,]+', value, maxsplit=1)
        self.firmware_name = parts[0]
        self.firmware_version = parts[1] if len(parts) > 1 else ""

        # Auto-detect, but do not override an explicit --model setting.
        if self.model == "auto":
            if "PRO" in value.upper():
                self.model = "sa818pro"
            else:
                self.model = "sa818"

        if log:
            if self.firmware_version:
                logger.info('Firmware %s, version: %s', self.firmware_name, self.firmware_version)
            else:
                logger.info('Firmware %s', self.firmware_name)

        return {
            "raw": value,
            "firmware": self.firmware_name,
            "version": self.firmware_version,
            "model": self.model,
        }

    def rssi(self):
        # SA818Pro datasheet uses RSSI?, while some older tools use AT+RSSI?.
        for cmd in (self.RSSI_PRO, self.RSSI_LEGACY):
            reply = self.command(cmd, expect=("RSSI:", "RSSI="), timeout=1.5)
            if reply:
                break
        else:
            logger.error('No RSSI response')
            return None

        match = re.match(r'^RSSI[:=](\d+)$', reply)
        if not match:
            logger.error('Unable to decode RSSI response: %s', reply)
            return None

        rssi = int(match.group(1))
        logger.info('RSSI: %d', rssi)
        return rssi

    def read_group(self, log=True):
        reply = self.command(self.READGRP, expect=("+DMOREADGROUP:", "+DMOREADGROUP="), timeout=1.5)
        if not reply:
            if log:
                logger.error('No read-group response')
            return None

        # Official format text sometimes shows "=", examples show ":".
        match = re.match(r'^\+DMOREADGROUP[:=](.+)$', reply)
        if not match:
            if log:
                logger.error('Unable to decode read-group response: %s', reply)
            return None

        parts = match.group(1).split(',')
        if len(parts) != 6:
            if log:
                logger.error('Unexpected read-group format: %s', reply)
            return None

        group = {
            "bw": int(parts[0]),
            "tx_frequency": parts[1],
            "rx_frequency": parts[2],
            "tx_cxcss": parts[3],
            "squelch": int(parts[4]),
            "rx_cxcss": parts[5],
        }

        bw_label = ['Narrow', 'Wide'][group["bw"]] if group["bw"] in (0, 1) else str(group["bw"])
        if log:
            logger.info(
                "Group: BW=%s, TX=%s MHz, RX=%s MHz, TX_CXCSS=%s, SQ=%d, RX_CXCSS=%s",
                bw_label,
                group["tx_frequency"],
                group["rx_frequency"],
                group["tx_cxcss"],
                group["squelch"],
                group["rx_cxcss"],
            )
        return group

    @staticmethod
    def _same_frequency(a, b, tolerance_hz=1.0):
        try:
            return abs(float(a) - float(b)) * 1_000_000 <= tolerance_hz
        except (TypeError, ValueError):
            return False

    def group_matches(self, group, bw, tx_freq, rx_freq, tx_tone, squelch, rx_tone):
        if not group:
            return False

        return (
            int(group.get("bw")) == int(bw)
            and self._same_frequency(group.get("tx_frequency"), tx_freq)
            and self._same_frequency(group.get("rx_frequency"), rx_freq)
            and str(group.get("tx_cxcss")).strip() == str(tx_tone).strip()
            and int(group.get("squelch")) == int(squelch)
            and str(group.get("rx_cxcss")).strip() == str(rx_tone).strip()
        )

    def scan(self, frequency):
        cmd = f"S+{frequency:.4f}"
        reply = self.command(cmd, expect=("S=", "S:"), timeout=2.5)
        if not reply:
            logger.error('No scan response')
            return None

        match = re.match(r'^S[=:]([01])$', reply)
        if not match:
            logger.error('Unable to decode scan response: %s', reply)
            return None

        has_signal = match.group(1) == "0"
        logger.info("Scan %.4f MHz: %s", frequency, "signal present" if has_signal else "no signal")
        return has_signal

    def set_radio(self, opts):
        tone = opts.ctcss if opts.ctcss else opts.dcs
        if tone:
            tx_tone, rx_tone = tone
        else:
            tx_tone, rx_tone = ['0000', '0000']

        if opts.offset == 0.0:
            tx_freq = rx_freq = f"{opts.frequency:.4f}"
        else:
            rx_freq = f"{opts.frequency:.4f}"
            tx_freq = f"{opts.frequency + opts.offset:.4f}"

        warn_if_not_on_step(float(tx_freq), opts.bw)
        warn_if_not_on_step(float(rx_freq), opts.bw)

        effective_squelch = opts.squelch

        def make_args(sql):
            return f"{opts.bw},{tx_freq},{rx_freq},{tx_tone},{sql},{rx_tone}"

        def send_setgroup(args):
            # SA818Pro is documented with '=' and your module rejects ':' with
            # +DMOERROR, so do not use ':' as a fallback once PRO is detected.
            if self.model == "sa818pro":
                separators = ("=",)
            else:
                # Keep legacy behavior for older modules. The original W6BSD
                # script used ':', while the official programming manuals show '='.
                separators = (":", "=")

            last_response = None
            for sep in separators:
                cmd = f"{self.SETGRP}{sep}{args}"
                last_response = self.command(cmd, expect=("+DMOSETGROUP:", "+DMOERROR"), timeout=2.0)
                if last_response == '+DMOSETGROUP:0':
                    return last_response
                # If the module understood the command but reported invalid
                # data, do not try a syntax fallback. The parameters need fixing.
                if last_response == '+DMOSETGROUP:1':
                    return last_response
            return last_response

        response = send_setgroup(make_args(effective_squelch))

        # Field observation with SA818PRO_V1.0: the module may return
        # +DMOSETGROUP:1 even though the values were actually written.
        # For backward-compatible CLI behavior, verify by reading the stored
        # group back before treating this as a failure.
        confirmed_by_readback = False
        if self.model == "sa818pro" and response != '+DMOSETGROUP:0':
            time.sleep(0.2)
            group = self.read_group(log=False)
            if self.group_matches(group, opts.bw, tx_freq, rx_freq, tx_tone, effective_squelch, rx_tone):
                confirmed_by_readback = True
                logger.debug(
                    "SA818Pro returned %s after setgroup, but readback confirms the requested settings",
                    response
                )
                response = '+DMOSETGROUP:0'

        # Some PRO firmware may really reject SQ=8. If readback did not confirm
        # it, retry internally with SQ=7 while keeping the old CLI valid.
        if response == '+DMOSETGROUP:1' and self.model == "sa818pro" and opts.squelch == 8:
            logger.warning(
                "SA818Pro did not confirm squelch level 8; retrying internally with level 7"
            )
            effective_squelch = 7
            response = send_setgroup(make_args(effective_squelch))
            if response != '+DMOSETGROUP:0':
                time.sleep(0.2)
                group = self.read_group(log=False)
                if self.group_matches(group, opts.bw, tx_freq, rx_freq, tx_tone, effective_squelch, rx_tone):
                    confirmed_by_readback = True
                    logger.debug(
                        "SA818Pro returned %s after setgroup retry, but readback confirms the requested settings",
                        response
                    )
                    response = '+DMOSETGROUP:0'

        if response != '+DMOSETGROUP:0':
            if response == '+DMOSETGROUP:1':
                logger.error(
                    "SA818 programming error: module says a parameter is out of range "
                    "and readback did not confirm the requested settings "
                    "(BW=%s, TX=%s, RX=%s, TX tone=%s, SQL=%s, RX tone=%s)",
                    opts.bw, tx_freq, rx_freq, tx_tone, effective_squelch, rx_tone
                )
            else:
                logger.error('SA818 programming error, got: %s', response)
            return False

        if confirmed_by_readback:
            logger.info("SA818Pro setgroup confirmed by readback")

        bw_label = ['Narrow', 'Wide'][opts.bw]
        if opts.ctcss:
            logger.info(
                "%s, BW: %s, Frequency (RX: %s / TX: %s), CTCSS (TX: %s / RX: %s), squelch: %s, OK",
                response, bw_label, rx_freq, tx_freq,
                CTCSS[int(tx_tone)], CTCSS[int(rx_tone)], effective_squelch
            )
        elif opts.dcs:
            logger.info(
                "%s, BW: %s, Frequency (RX: %s / TX: %s), DCS (TX: %s / RX: %s), squelch: %s, OK",
                response, bw_label, rx_freq, tx_freq,
                tx_tone, rx_tone, effective_squelch
            )
        else:
            logger.info(
                "%s, BW: %s, RX frequency: %s, TX frequency: %s, squelch: %s, OK",
                response, bw_label, rx_freq, tx_freq, effective_squelch
            )

        if opts.tail is not None and opts.ctcss is not None:
            self.tail(opts)
        elif opts.tail is not None:
            logger.warning('Ignoring "--tail" specified without CTCSS')

        return True

    def set_filter(self, opts):
        # Keep old SA818 CLI behavior.  Some SA818Pro firmware accepts the
        # legacy filter command even though it is not consistently documented.
        # Therefore we send it and trust the chip response instead of skipping it.
        for key in ("emphasis", "highpass", "lowpass"):
            if getattr(opts, key) is None:
                setattr(opts, key, 1)

        state = {0: 'enabled', 1: 'disabled'}
        cmd = f"{self.FILTER}={opts.emphasis},{opts.highpass},{opts.lowpass}"
        response = self.command(cmd, expect=("+DMOSETFILTER:", "+DMOERROR"), timeout=2.0)

        if response != "+DMOSETFILTER:0":
            logger.error('SA818 set filter error, got: %s', response)
            return False

        logger.info(
            "%s filters [Pre/De]emphasis: %s, high-pass: %s, low-pass: %s",
            response, state[opts.emphasis], state[opts.highpass], state[opts.lowpass]
        )
        return True

    def set_volume(self, opts):
        cmd = f"{self.VOLUME}={opts.level:d}"
        response = self.command(cmd, expect=("+DMOSETVOLUME:",), timeout=2.0)
        if response != "+DMOSETVOLUME:0":
            logger.error('SA818 set volume error, got: %s', response)
            return False

        logger.info("%s Volume level: %d, OK", response, opts.level)
        return True

    def tail(self, opts):
        state = {True: "open/on", False: "closed/off"}
        cmd = f"{self.TAIL}={int(opts.tail)}"
        response = self.command(cmd, expect=("+DMOSETTAIL:",), timeout=2.0)
        if response != "+DMOSETTAIL:0":
            logger.error('SA818 set tail error, got: %s', response)
            return False

        logger.info("%s tail: %s", response, state[opts.tail])
        return True

    def send_sms(self, message):
        if len(message.encode("ascii", errors="ignore")) != len(message):
            raise ValueError("SA818Pro SMS payload must be ASCII")

        if len(message) > 19:
            raise ValueError("SA818Pro SMS payload is limited to 19 bytes")

        cmd = f"{self.TXOMES}={message}"
        response = self.command(cmd, expect=("+TXOMES:",), timeout=3.0)
        if response != "+TXOMES:0":
            logger.error("SMS send error, got: %s", response)
            return False

        logger.info("%s SMS sent: %s", response, message)
        return True


def frequency_in_module_range(freq):
    return (134.0 <= freq <= 174.0) or (400.0 <= freq <= 470.0)


def frequency_in_amateur_range(freq):
    # Original W6BSD script used 144-148 and 420-450.
    # This is kept only as an optional extra guard, not as the default,
    # because SA818Pro modules themselves support wider VHF/UHF ranges.
    return (144.0 <= freq <= 148.0) or (420.0 <= freq <= 450.0)


def type_frequency(parg):
    try:
        frequency = float(parg)
    except ValueError:
        raise argparse.ArgumentTypeError("Invalid frequency") from None

    if not frequency_in_module_range(frequency):
        raise argparse.ArgumentTypeError(
            "Frequency outside SA818Pro module range "
            "(134-174 MHz or 400-470 MHz)"
        )
    return frequency


def type_ctcss(parg):
    err_msg = 'Invalid CTCSS. Use --help for the list of CTCSS tones.'
    tone_codes = []
    codes = parg.split(',')
    if len(codes) == 1:
        codes.append(codes[0])
    elif len(codes) > 2:
        raise argparse.ArgumentTypeError(err_msg)

    for code in codes:
        try:
            ctcss = str(float(code))
            if ctcss not in CTCSS:
                raise ValueError
            ctcss_index = CTCSS.index(ctcss)
            tone_codes.append(f"{ctcss_index:04d}")
        except ValueError:
            raise argparse.ArgumentTypeError(err_msg) from None

    return tone_codes


def type_dcs(parg):
    err_msg = 'Invalid DCS. Use --help for the list of DCS codes.'
    dcs_codes = []
    codes = parg.split(',')
    if len(codes) == 1:
        codes.append(codes[0])
    elif len(codes) > 2:
        raise argparse.ArgumentTypeError(err_msg)

    for code in codes:
        code = code.upper().strip()
        if len(code) < 2 or code[-1] not in ('N', 'I'):
            raise argparse.ArgumentTypeError(err_msg)

        number, direction = code[:-1], code[-1]
        try:
            dcs = f"{int(number):03d}"
        except ValueError:
            raise argparse.ArgumentTypeError(err_msg) from None

        if dcs not in DCS_CODES:
            raise argparse.ArgumentTypeError(err_msg)

        dcs_codes.append(dcs + direction)

    return dcs_codes


def type_squelch(parg):
    try:
        value = int(parg)
    except ValueError:
        raise argparse.ArgumentTypeError("Invalid squelch value") from None

    if value not in range(0, 9):
        raise argparse.ArgumentTypeError('Squelch must be between 0 and 8 inclusive')
    return value


def type_level(parg):
    try:
        value = int(parg)
    except ValueError:
        raise argparse.ArgumentTypeError("Invalid volume level") from None

    if value not in range(1, 9):
        raise argparse.ArgumentTypeError('Volume must be between 1 and 8 inclusive')
    return value


def enabledisable(parg):
    value = parg.lower()
    if value == 'enable':
        return 0
    if value == 'disable':
        return 1
    raise argparse.ArgumentTypeError("Possible values are Enable or Disable") from None


def openclose(parg):
    if parg is None:
        return None

    value = parg.lower().strip()
    if "open".startswith(value):
        return True
    if "close".startswith(value):
        return False

    raise argparse.ArgumentTypeError("Possible values are Open or Close") from None


def warn_if_not_on_step(freq, bw):
    # Narrow: 12.5 kHz grid. Wide: 25 kHz grid.
    step_mhz = 0.0125 if bw == 0 else 0.025
    n = round(freq / step_mhz)
    error_hz = abs(freq - n * step_mhz) * 1_000_000
    if error_hz > 1.0:
        logger.warning(
            "%.4f MHz is not on the expected %.1f kHz channel grid",
            freq, step_mhz * 1000
        )


def set_loglevel():
    loglevel = os.getenv('LOGLEVEL', 'INFO').upper()
    try:
        logger.root.setLevel(loglevel)
    except ValueError:
        logger.warning('Loglevel error: %s', loglevel)


def format_codes():
    ctcss = textwrap.wrap(', '.join(CTCSS[1:]))
    dcs = textwrap.wrap(', '.join(DCS_CODES))

    codes = (
        "You can specify different transmit and receive codes by separating "
        "them with a comma.\n",
        "> Example: --ctcss 94.8,127.3 or --dcs 043N,047N\n\n",
        f"CTCSS codes (PL tones)\n{chr(10).join(ctcss)}",
        "\n\n",
        "DCS codes:\n"
        "DCS codes must be followed by N or I for Normal or Inverse:\n",
        f"> Example: 047I\n{chr(10).join(dcs)}"
    )
    return ''.join(codes)


def command_parser():
    parser = argparse.ArgumentParser(
        description="Program SA818 / SA818Pro radio modules",
        epilog=format_codes(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--debug", action="store_true", default=False)
    parser.add_argument("--port", type=str,
                        help="Serial port [default: /dev/serial0, then /dev/ttyUSB0]")
    parser.add_argument('--speed', type=int, choices=BAUD_RATES, default=DEFAULT_BAUDRATE,
                        help="Connection speed [default: %(default)s]")
    parser.add_argument('--model', choices=("auto", "sa818", "sa818pro"), default="auto",
                        help="Module model [default: %(default)s]")
    parser.add_argument('--strict-amateur', action="store_true", default=False,
                        help="Reject frequencies outside 144-148 / 420-450 MHz")

    subparsers = parser.add_subparsers(dest="func")

    p_radio = subparsers.add_parser("radio", help='Program the radio')
    p_radio.add_argument('--bw', type=int, choices=(0, 1), default=1,
                         help="Bandwidth 0=NARROW (12.5 kHz), 1=WIDE (25 kHz) [default: WIDE]")
    p_radio.add_argument("--frequency", required=True, type=type_frequency,
                         help="Receive frequency in MHz")
    p_radio.add_argument("--offset", default=0.0, type=float,
                         help="TX offset in MHz, 0 for simplex [default: %(default)s]")
    p_radio.add_argument("--squelch", type=type_squelch, default=4,
                         help="Squelch value 0 to 8 [default: %(default)s]")
    code_group = p_radio.add_mutually_exclusive_group()
    code_group.add_argument("--ctcss", default=None, type=type_ctcss,
                            help="CTCSS / PL tone. Use 0 for no CTCSS [default: %(default)s]")
    code_group.add_argument("--dcs", default=None, type=type_dcs,
                            help="DCS code number followed by N or I [default: %(default)s]")
    p_radio.add_argument("--tail", default=None, type=openclose,
                         help="CTCSS tail tone Open/Close")

    p_volume = subparsers.add_parser("volume", help="Set the volume level")
    p_volume.add_argument("--level", type=type_level, default=4,
                          help="Volume value 1 to 8 [default: %(default)s]")

    p_filter = subparsers.add_parser("filters", aliases=['filter'], help="Enable/Disable legacy filters")
    p_filter.add_argument("--emphasis", type=enabledisable,
                          help="[Pre/De]-emphasis Enable/Disable [default: disable]")
    p_filter.add_argument("--highpass", type=enabledisable,
                          help="High-pass filter Enable/Disable [default: disable]")
    p_filter.add_argument("--lowpass", type=enabledisable,
                          help="Low-pass filter Enable/Disable [default: disable]")

    subparsers.add_parser("version", help="Show firmware version")
    subparsers.add_parser("rssi", help="Show RSSI")
    subparsers.add_parser("readgroup", help="Read current radio parameters")

    p_scan = subparsers.add_parser("scan", help="SA818Pro sweep/scan one frequency")
    p_scan.add_argument("--frequency", required=True, type=type_frequency,
                        help="Frequency to scan in MHz")

    p_sms = subparsers.add_parser("sms", help="SA818Pro send short data message")
    p_sms.add_argument("message", help="ASCII payload, max 19 bytes")

    opts = parser.parse_args()

    if not opts.func:
        parser.error('the following arguments are required: {radio,volume,filters,version,rssi,readgroup,scan,sms}')

    if opts.strict_amateur:
        freqs = []
        if hasattr(opts, "frequency"):
            freqs.append(opts.frequency)
        if hasattr(opts, "offset") and opts.offset:
            freqs.append(opts.frequency + opts.offset)

        for freq in freqs:
            if not frequency_in_amateur_range(freq):
                parser.error(
                    f"{freq:.4f} MHz is outside the script's optional amateur guard "
                    "(144-148 / 420-450 MHz)"
                )

    return opts


def main():
    set_loglevel()
    opts = command_parser()

    if opts.debug:
        logger.setLevel(logging.DEBUG)

    logger.debug(opts)

    try:
        radio = SA818(opts.port, opts.speed, opts.model)
    except (IOError, SystemError, ValueError) as err:
        raise SystemExit(err) from None

    try:
        if opts.func == 'version':
            radio.version()
        elif opts.func == 'rssi':
            radio.rssi()
        elif opts.func == 'readgroup':
            radio.read_group()
        elif opts.func == 'scan':
            radio.scan(opts.frequency)
        elif opts.func == 'sms':
            radio.send_sms(opts.message)
        elif opts.func == 'radio':
            radio.set_radio(opts)
        elif opts.func == 'filters':
            for key in ('emphasis', 'highpass', 'lowpass'):
                if getattr(opts, key) is not None:
                    break
            else:
                raise SystemExit('filters need at least one argument') from None
            radio.set_filter(opts)
        elif opts.func == 'volume':
            radio.set_volume(opts)
    finally:
        radio.close()


if __name__ == "__main__":
    main()
