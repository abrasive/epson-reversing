# Super crude implementation of IEEE 1284.4.
# Speaks the language but doesn't support multiple simultaneous transfers.

from collections import defaultdict, namedtuple
import struct
import os
import time
import binascii
from dataclasses import dataclass
import logging

log = logging.getLogger('dirty4')

D4Packet = namedtuple('D4Packet', ['psid', 'ssid', 'payload', 'credit', 'oob', 'eom'],
                                defaults=[   None,       b'',        1, False, False],
                             )

commands = {
        'Init': 0,
        'OpenChannel': 1,
        'CloseChannel': 2,
        'Credit': 3,
        'CreditRequest': 4,
        'Exit': 8,
        'GetSocketID': 9,
        }

errors = {
        0x80: 'Malformed packet',
        0x81: 'No credit',
        0x82: 'Reply without command',
        0x83: 'Packet too big',
        0x84: 'Channel not open',
        0x85: 'Unknown Result',
        0x86: 'Credit overflow',
        0x87: 'Bad command/reply',
        }

@dataclass
class DirtyChannel:
    sid: int
    mtu: int
    credits: int = 0

class DirtyChannelContext(object):
    def __init__(self, d4, name):
        self.d4 = d4
        self.name = name

    def __enter__(self):
        self.sid = self.d4.GetSocketID(self.name)
        self.chan = self.d4.OpenChannel(self.sid)
        return self

    def __exit__(self, a, b, c):
        self.d4.CloseChannel(self.sid)

    @property
    def credits(self):
        return self.chan.credits

    def ensure_credit(self):
        if self.credits < 1:
            while self.d4.CreditRequest(self.sid) < 1:
                time.sleep(0.1)

    def write(self, data, progress=None):
        while len(data):
            if len(data) > self.chan.mtu - 6:
                payload = data[:self.chan.mtu - 6]
                eom = False
            else:
                payload = data
                eom = True

            data = data[len(payload):]

            self.ensure_credit()
            self.d4.write_packet(self.sid, eom=eom, payload=payload)

            if progress:
                progress(len(payload))

    def read(self):
        self.d4.Credit(self.sid, 1)
        return self.d4.read_packet()

    def cmd2(self, name, payload, binary=False):
        assert len(name) == 2
        if isinstance(payload, int):
            payload = bytearray([payload])
        self.write(name.encode('ascii') + struct.pack('<H', len(payload)) + payload)
        result = self.read().payload
        if binary:
            return result
        else:
            return result.decode('ascii')

class Dirty4(object):
    def __init__(self, port):
        self.port = port
        self.buffer = b''

        self.channels = {}
        self.channels[0] = DirtyChannel(sid=0, mtu=64)

        # drain eg. periodic status messages that may be queued
        os.read(port, 131072)

        # escape other modes, enter 1284.4 mode
        self._write(b'\x00\x00\x00\x1b\x01@EJL 1284.4\n@EJL\n@EJL\n')
        self._read(8)

        self.Init()

    def _write(self, data):
        while len(data):
            written = os.write(self.port, data)
            if written == 0:
                time.sleep(0.1)
            data = data[written:]

    def _read(self, length):
        while len(self.buffer) < length:
            new = os.read(self.port, 1024)
            if len(new) == 0:
                time.sleep(0.1)
            self.buffer += new

        result = self.buffer[:length]
        self.buffer = self.buffer[length:]
        return result

    def write_packet(self, psid, **kwargs):
        command = D4Packet(psid, **kwargs)

        ssid = command.ssid
        if ssid is None:
            ssid = command.psid

        control = 0
        if command.eom:
            control |= 2
        if command.oob:
            control |= 1

        length = 6 + len(command.payload)

        header = struct.pack('>BBHBB', command.psid, ssid, length, command.credit, control)

        data = header + command.payload
        log.debug('> ' + ' '.join('%02x' % x for x in data[:0x100]))
        self._write(data)

        if self.channels[command.psid].credits:
            self.channels[command.psid].credits -= 1

    def read_packet(self):
        header_data = self._read(6)
        psid, ssid, length, credit, control = struct.unpack('>BBHBB', header_data)

        if length > self.channels[psid].mtu:
            raise ValueError("Received garbage data")

        payload = self._read(length-6)

        log.debug('< ' + ' '.join('%02x' % x for x in header_data + payload))

        self.channels[psid].credits += credit

        return D4Packet(psid, ssid, payload, credit, bool(control & 1), bool(control & 2))

    def command(self, name, payload=b''):
        if name not in ['Init', 'Exit']:
            assert self.channels[0].credits

        log.debug("%s %s" % (name, binascii.hexlify(payload)))
        cmd_byte = commands[name]
        self.write_packet(0, payload=bytearray([cmd_byte]) + payload)
        resp = self.read_packet()

        assert resp.psid == 0

        resp_bytes = bytearray(resp.payload)

        if resp_bytes[0] == 0x7f:   # Error
            print("ERROR:" + errors.get(resp_bytes[3], '0x%x' % resp_bytes[3]))

        assert resp_bytes[0] == cmd_byte | 0x80
        assert resp_bytes[1] == 0

        return resp.payload[2:]

    def Init(self):
        resp = self.command("Init", b'\x10')
        assert resp == b'\x10'

    def Exit(self):
        self.command("Exit")

    def GetSocketID(self, name):
        resp = self.command("GetSocketID", name.encode('ascii'))
        return int(resp[0])

    def OpenChannel(self, sid):
        req = struct.pack('>BBHHHH', sid, sid, 0xffff, 0xffff, 0xffff, 0xffff)
        resp = self.command("OpenChannel", req)
        psid, ssid, mtu, max_credit, credit = struct.unpack('>BBHHH', resp)
        self.channels[sid] = DirtyChannel(sid=psid, mtu=mtu, credits=credit)
        return self.channels[sid]

    def CloseChannel(self, sid):
        req = struct.pack('>BB', sid, sid)
        self.command("CloseChannel", req)

    def Credit(self, sid, amount):
        req = struct.pack('>BBH', sid, sid, amount)
        self.command("Credit", req)

    def CreditRequest(self, sid, amount=0x100):
        req = struct.pack('>BBH', sid, sid, amount)
        resp = self.command("CreditRequest", req)
        _, _, amount = struct.unpack('>BBH', resp)
        self.channels[sid].credits += amount
        return amount

    def channel(self, name):
        return DirtyChannelContext(self, name)

if __name__ == "__main__":
    path = '/dev/usb/lp0'
    port = os.open(path, os.O_RDWR | os.O_SYNC)
    d4 = Dirty4(port)

    # Retrieve a bunch of version & status info

    with d4.channel("EPSON-CTRL") as chan:
        for i in range(8):
            print(i, chan.cmd2('vi', i)[6:-2])

        print("di:", repr(chan.cmd2('di', 1)))

        st2 = chan.cmd2('st', 1, binary=True)
        assert st2.startswith(b'@BDC ST2\r\n')
        length, = struct.unpack('<H', st2[10:12])
        st2 = st2[12:]
        assert length == len(st2)

        while len(st2):
            typ, length = struct.unpack('BB', st2[:2])
            payload = st2[2:2+length]
            st2 = st2[2+length:]
            print(hex(typ), binascii.hexlify(payload))
