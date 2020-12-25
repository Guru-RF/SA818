#!/usr/bin/env python
# Fred (W6BSD)
#

__doc__ = """sa818"""

import argparse
import logging
import os
import sys
import textwrap
import time

import serial

logging.basicConfig(format='%(name)s: %(levelname)s: %(message)s',
                    level=logging.INFO)
logger = logging.getLogger('SA818')


CTCSS = (
  "None", "67.0", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4", "88.5", "91.5",
  "94.8", "97.4", "100.0", "103.5", "107.2", "110.9", "114.8", "118.8", "123.0",
  "127.3", "131.8", "136.5", "141.3", "146.2", "151.4", "156.7", "162.2",
  "167.9", "173.8", "179.9", "186.2", "192.8", "203.5", "210.7", "218.1",
  "225.7", "233.6", "241.8", "250.3"
)

DCS_CODES = [
  "023", "025", "026", "031", "032", "036", "043", "047", "051", "053",	"054", "065", "071",
  "072", "073", "074", "114", "115", "116", "122", "125", "131", "132", "134", "143", "145",
  "152", "155", "156", "162",	"165", "172", "174", "205", "212", "223", "225", "226", "243",
  "244", "245", "246", "251", "252", "255", "261", "263", "265", "266", "271", "274", "306",
  "311", "315", "325", "331", "332", "343", "346", "351",	"356", "364", "365", "371", "411",
  "412", "413", "423", "431", "432", "445", "446", "452", "454", "455", "462", "464", "465",
  "466", "503",	"506", "516", "523", "526", "532", "546", "565", "606", "612", "624",	"627",
  "631", "632", "654", "662", "664", "703", "712", "723", "731", "732", "734", "743", "754"
]

DEFAULT_BAUDRATE = 9600

class SA818:
  EOL = "\r\n"
  INIT = "AT+DMOCONNECT"
  SETGRP = "AT+DMOSETGROUP"
  FILTER = "AT+SETFILTER"
  NARROW = 0
  WIDE = 1
  PORTS = ('/dev/ttyAMA0', '/dev/ttyUSB0')
  READ_TIMEOUT = 1.0

  def __init__(self, port=None):
    self.serial = None
    if port:
      ports = [port]
    else:
      ports = self.PORTS

    for _port in ports:
      try:
        self.serial = serial.Serial(port=_port, baudrate=DEFAULT_BAUDRATE,
                                    parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                    bytesize=serial.EIGHTBITS,
                                    timeout=self.READ_TIMEOUT)
        break
      except serial.SerialException as err:
        logger.debug(err)

    if not isinstance(self.serial, serial.Serial):
      raise IOError('Error openning the serial port')

    self.send(self.INIT)
    reply = self.readline()
    if reply != "+DMOCONNECT:0":
      raise SystemError('Connection error')

    return

  def close(self):
    self.serial.close()

  def send(self, *args):
    data = ','.join(args)
    logger.debug('Sending: %s', data)
    data = bytes(data + self.EOL, 'ascii')
    try:
      self.serial.write(data)
    except Exception as err:
      logger.error(err)

  def readline(self):
    try:
      line = self.serial.readline()
    except Exception as err:
      logger.warning(err)
      return
    line = line.decode('ascii')
    logger.debug(line)
    return line.rstrip()

  def version(self):
    self.send("AT+VERSION")
    time.sleep(0.5)
    reply = self.readline()
    try:
      _, version = reply.split('_')
    except ValueError:
      logger.error('Unable to decode the firmeare version')
    else:
      logger.info('Firmware version: %s', version)
    return version

  def set_frequency(self, opts):
    tone = opts.ctcss if opts.ctcss else opts.dcs
    if not tone:                # 0000 = No ctcss or dcs tone
      tone = '0000'

    cmd = "{}={},{},{},{},{},{}".format(self.SETGRP, self.WIDE, opts.frequency,
                                        opts.frequency, tone, opts.squelsh,
                                        tone)
    self.send(cmd)
    time.sleep(1)
    response = self.readline()
    if response != '+DMOSETGROUP:0':
	    logger.error('SA818 programming error')
    else:
      logger.info("%s frequency: %s, tone: %s, squelsh: %s, OK",
                  response, opts.frequency, tone, opts.squelsh)

  def set_filter(self, opts):
    _yn = {True: "Yes", False: "No"}
    # filters are pre-empasys, high-pass, low-pass
    cmd = "{}={},{},{}".format(self.FILTER, int(not opts.emphasis),
                               int(not opts.highpass), int(not opts.lowpass))
    self.send(cmd)
    time.sleep(1)
    response = self.readline()
    if response != "+DMOSETFILTER:0":
      logger.error('SA818 programming error')
    else:
      logger.info("%s filters [Pre/De]emphasis: %s, high-pass: %s, low-pass: %s",
                  response, _yn[opts.emphasis], _yn[opts.highpass], _yn[opts.lowpass])

def type_frequency(parg):
  try:
    frequency = float(parg)
  except ValueError:
    raise argparse.ArgumentTypeError

  if not (144 < frequency < 148) and not (420 < frequency < 450):
    logger.error('Frequency outside the amateur bands')
    raise argparse.ArgumentError

  return "{:.4f}".format(frequency)

def type_ctcss(parg):
  err_msg = 'Invalid CTCSS use the --help argument for the list of CTCSS'
  try:
    ctcss = str(float(parg))
  except ValueError:
    raise argparse.ArgumentTypeError

  if ctcss not in CTCSS:
    logger.error(err_msg)
    raise argparse.ArgumentError

  tone_code = CTCSS.index(ctcss)
  return "{:04d}".format(tone_code)

def type_dcs(parg):
  err_msg = 'Invalid DCS use the --help argument for the list of DCS'
  try:
    dcs = str(float(parg))
  except ValueError:
    raise argparse.ArgumentTypeError

  if dcs not in DCS:
    logger.error(err_msg)
    raise argparse.ArgumentError

  dcs_code = DCS.index(dcs)
  return "{:04d}".format(dcs_code)

def type_range(parg):
  try:
    value = int(parg)
  except:
    raise argparse.ArgumentTypeError

  if value not in range(1, 10):
    logger.error('The value must must be between 1 and 9')
    raise argparse.ArgumentError

  return value

def yesno(parg):
  yes_strings = ["y", "yes", "true", "1", "on"]
  no_strings = ["n", "no", "false", "0", "off"]
  if parg.lower() in yes_strings:
    return True
  if parg.lower() in no_strings:
    return False
  raise argparse.ArgumentError
  return True

def set_loglevel():
  loglevel = os.getenv('LOGLEVEL', 'INFO')
  loglevel = loglevel.upper()
  try:
    logger.root.setLevel(loglevel)
  except ValueError:
    logger.warning('Loglevel error: %s', loglevel)


def format_codes():
  ctcss = textwrap.wrap(', '.join(CTCSS[1:]))
  dcs = textwrap.wrap(', '.join(DCS_CODES))

  codes = "CTCSS codes (PL Tones):\n{}\n\nDCS Codes:\n{}".format(
    '\n'.join(ctcss), '\n'.join(dcs))
  return codes

def main():
  set_loglevel()
  parser = argparse.ArgumentParser(
    description="generate configuration for switch port",
    epilog=format_codes(),
    formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  code_group = parser.add_mutually_exclusive_group()
  parser.add_argument("--frequency", required=True, type=type_frequency,
                      help="Transmit frequency")
  parser.add_argument("--offset", default=0.0,
                      help="0.0 for no offset [default: %(default)s]")
  code_group.add_argument("--ctcss", default=None, type=type_ctcss,
                          help="CTCSS (PL Tone) 0 for no CTCSS [default: %(default)s]")
  code_group.add_argument("--dcs", default=None, type=type_dcs,
                          help="DCS code CTCSS [default: %(default)s]")
  parser.add_argument("--squelsh", type=type_range, default=4,
                      help="Squelsh value (1 to 9) [default: %(default)s]")
  parser.add_argument("--volume", type=type_range, default=4,
                      help="Volume value (1 to 8) [default: %(default)s]")
  parser.add_argument("--emphasis", type=yesno, default='no',
                      help="Enable [Pr/De]-emphasis (yes/no) [default: %(default)s]")
  parser.add_argument("--highpass", type=yesno, default='no',
                      help="Enable high pass filter (yes/no) [default: %(default)s]")
  parser.add_argument("--lowpass", type=yesno, default='no',
                      help="Enable low pass filters (yes/no) [default: %(default)s]")

  parser.add_argument("--port", type=str,
                      help="Serial port [default: linux console port]")
  parser.add_argument("--debug", action="store_true", default=False)

  opts = parser.parse_args()

  if opts.debug:
    logger.setLevel(logging.DEBUG)

  try:
    radio = SA818()
  except IOError as err:
    logger.error(err)
    sys.exit(os.EX_IOERR)

  radio.set_frequency(opts)
  radio.set_filter(opts)
  radio.version()

if __name__ == "__main__":
  main()
