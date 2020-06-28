# unCROM

Recent Epson printers compress some parts of their firmware using a proprietary compression method.
This can be recognised by a 4-byte magic, ASCII `CROM`.
The `uncrom` tool allows you to unpack these.

Usage:

```
./uncrom.py my_crom_file
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

# epsflasher

This tool allows you to flash .rcx format firmware to your printer via USB.

It speaks the IEEE 1284.4 multiplexing protocol (aka Dot4 or D4),
although it doesn't actually support multiple open channels at a time.

You can load firmware in normal operating mode, or in some recovery modes.
You may not be able to flash an entire .rcx in recovery mode.

## Printer recovery mode

These printers have multiple bootloader/recovery modes which can be accessed by holding down a key combo at power on.
A surprising number of these can be found [on YouTube](https://www.youtube.com/watch?v=36bkBq_aOxI),
because some people are flogging a modded firmware that ignores empty or unlicensed ink cartridges.

On my XP-240, which has no LCD and six buttons on the front panel, you turn the printer off, then hold the two rightmost and two leftmost buttons (stop, colour copy, wifi, and power) for 2 seconds.
The LEDs then turn on in a unique blink pattern to show it's in this mode.
I think this mode runs the main firmware but doesn't initialise all the hardware; I call this "safe mode".

There is a second recovery mode as well, accessed by stop, colour copy, info, and power.
This one seems to be the actual bootloader - the device enumerates with a different USB ID (04b8:0007).
So I call this "bootloader mode" to avoid confusion.
(This bootloader can be found early in the second segment of the first EPSON IPL of the firmware.)

My printer firmware has two parts in the RCX: the second appears, perhaps, to be scanner firmware.
Each starts with an EPSON IPL header and their lengths are stored in the RCX header.

If I try and flash the whole thing in safe mode, the printer crashes.
But if I flash only the first block, it seems to succeed.
So a switch to `epsflasher`, `--only-first-block`, allows you to just flash the first block, if this is what you need.

I don't yet know how to detect that a printer is in safe mode.
Recovery mode is easy; it will respond to any `vi` command for version info with `vi:NA`.
