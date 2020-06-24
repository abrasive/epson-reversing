#!/usr/bin/env python

import struct
import sys

class CROMReader(object):
    def __init__(self, stream):
        self.stream = stream

        assert self.stream.read(4) == b'CROM'

    def unpack_all(self):
        while True:
            try:
                yield self.unpack()
            except EOFError:
                return

    def unpack(self):
        self.read_data()
        self.unpack_huffman_tables()
        self.decompress_copy_items()
        return self.execute_items()

    def take_marker(self, want_mark):
        mark, length = struct.unpack('>HH', self.stream.read(4))
        if mark != want_mark:
            raise ValueError("Expected mark %04x, got bytes %04x" % (want_mark, mark))

        return self.stream.read(length - 2)

    def read_data(self):
        soi = self.stream.read(6)
        if len(soi) < 6:
            raise EOFError()
        soi_mark, length = struct.unpack('>HL', soi)
        assert soi_mark == 0xffd8

        self.huffman_table_data = self.take_marker(0xffc4)

        copy_data_info = self.take_marker(0xffb1)
        coded_bytes, num_items = struct.unpack('>LL', copy_data_info[1:9])

        self.compressed_copy_data = self.stream.read(coded_bytes)
        self.num_copy_items = num_items

        literal_info = self.take_marker(0xffb2)
        literal_len, = struct.unpack('>L', literal_info)
        self.literal_data = self.stream.read(literal_len)

    def unpack_huffman_tables(self):
        data = iter(bytearray(self.huffman_table_data))
        tables = [tuple(self.unpack_huffman_table(data, i)) for i in range(3)]
        self.huffman_tables = tuple(tables)

    def unpack_huffman_table(self, data, index):
        # These are identical to JPEG DHTs: 16 counts, one for each codeword length, followed by symbols.

        ident = next(data)
        assert ident == 0xf0 + index

        counts = [next(data) for i in range(16)]

        table = [None] * 0x10000

        code = 0

        for length in range(1, 17):
            for _ in range(counts[length-1]):
                symbol = next(data)
                entry = (length, symbol)

                # write all table entries whose prefix starts with the code
                start = code << (16-length)
                num_entries = 0x10000 >> length
                for i in range(start, start+num_entries):
                    table[i] = entry

                code += 1
            code <<= 1

        return table

    def decompress_copy_items(self):
        data = iter(self.compressed_copy_data)
        self.copy_items = []

        buf_bits = 0
        buf = 0

        for _ in range(self.num_copy_items):
            item = []
            self.copy_items.append(item)
            for table in self.huffman_tables:
                while buf_bits < 16:
                    buf <<= 8
                    buf |= next(data)
                    buf_bits += 8
                buf &= 0xffffff   # just to avoid it growing without bound

                index = (buf >> (buf_bits - 16)) & 0xffff
                length, symbol = table[index]
                item.append(symbol)
                buf_bits -= length

    def execute_items(self):
        out = bytearray()
        literal_ptr = 0

        import tqdm
        for control, offsetl, offseth in tqdm.tqdm(self.copy_items):
            offset = offsetl | (offseth << 8)

            if not offset:
                if control == 0xff:
                    continue

                count = control + 1
                out.extend(self.literal_data[literal_ptr:literal_ptr+count])
                literal_ptr += count

            else:
                count = control + 2
                while count:
                    if count > offset:   # overlapping copies
                        copy = offset
                    else:
                        copy = count
                    end = -offset + copy
                    if end == 0:
                        end = None
                    out.extend(out[-offset:end])
                    count -= copy

        return out

if __name__ == "__main__":
    filename = sys.argv[1]
    crom = CROMReader(open(filename, "rb"))
    for i, data in enumerate(crom.unpack_all()):
        outname = filename + '.%d.bin' % i
        open(outname, 'wb').write(data)
