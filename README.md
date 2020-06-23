# unCROM

Recent Epson printers compress some parts of their firmware using a proprietary compression method.
This can be recognised by a 4-byte magic, ASCII `CROM`.
The `uncrom` tool allows you to unpack these.

Usage:

```
make
./uncrom my_crom_file
```

This will produce `my_crom_file.0.bin` with the uncompressed data.
If the CROM file contains multiple segments, then files `.1.bin`, `.2.bin` and so forth will be produced.

## File format

The file format is actually very close to JPEG - it uses tag-length-value markers to delineate several regions.

Data is compressed with an LZ77 style sliding window approach:
a series of items describe whether output data should be taken from the history,
or copied from a literal region.

The items - at 3 bytes each - are then themselves compressed:
a separate Huffman tree is made for each byte.
The Huffman data is stored just like a JPEG DHT, even down to the marker value.

Curiously, the ARM code that decodes CROMs in the printer doesn't actually decode the Huffman tables.
Instead, it passes it off to somewhere else - either a hardware decoder, or one of the Xtensa cores.
(The REALOID-based printer I dug this out of uses a pile of Xtensa cores to replace fixed-function image processing hardware.)

# rcx_correct

This tool corrects the checksums (sums and SHA-1) in .rcx files so the printer will accept them.
