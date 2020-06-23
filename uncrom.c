/*
 * Decompress an Epson compressed CROM.
 * James Wah 2020
 * This file is in the public domain.
 */

#define _GNU_SOURCE // for asprintf()

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>

void warn(const char *format, ...) {
    va_list ap;
    fprintf(stderr, "WARNING: ");
    va_start(ap, format);
    vfprintf(stderr, format, ap);
    va_end(ap);
}

void die(const char *format, ...) {
    va_list ap;
    fprintf(stderr, "ERROR: ");
    va_start(ap, format);
    vfprintf(stderr, format, ap);
    va_end(ap);

    exit(1);
}

void sread(FILE *fp, void *buf, size_t len) {
    size_t nread = fread(buf, 1, len, fp);
    if (nread != len)
        die("ERROR: file ended early (wanted %lu bytes, got %lu)\n", len, nread);
}

uint16_t read16be(FILE *fp) {
    uint16_t data;
    sread(fp, &data, sizeof(data));
    return ntohs(data);
}

uint32_t read32be(FILE *fp) {
    uint32_t data;
    sread(fp, &data, sizeof(data));
    return ntohl(data);
}

// top half: # prefix bits; bottom half: symbol
typedef uint16_t huff_entry_t;

struct crom {
    // these chunks come from the input file
    uint8_t *huffdata;
    uint8_t *compressed;
    uint8_t *literaldata;

    int num_items;

    // these are calculated from huffdata
    huff_entry_t hufftable[3][0x10000];

    // these are unpacked from compressed
    uint8_t *items;

    struct decomp_state {
        uint8_t *in;
        uint8_t *out;
        uint32_t buffer;
        int buffer_bits;
    } decompressor;
};

void read_crom_data(FILE *fp, struct crom *crom) {
    uint16_t tag;
    tag = read16be(fp);
    if (tag != 0xffd8)  // JPEG Start Of Image
        warn("Expected tag 0xffd8, got 0x%x instead\n", tag);

    uint32_t total_length = read32be(fp);

    tag = read16be(fp);
    if (tag != 0xffc4)  // JPEG Define Huffman Table
        warn("Expected tag 0xffc4, got 0x%x instead\n", tag);

    uint16_t huffdata_length = read16be(fp) - 2;
    crom->huffdata = malloc(huffdata_length);
    sread(fp, crom->huffdata, huffdata_length);

    tag = read16be(fp);
    if (tag != 0xffb1)
        warn("Expected tag 0xffb1, got 0x%x instead\n", tag);
    uint16_t copydata_length = read16be(fp) - 2;
    uint8_t *copydata = malloc(copydata_length);
    sread(fp, copydata, copydata_length);

    uint32_t coded_bytes = ntohl(*(uint32_t*)(copydata + 1));
    crom->num_items = ntohl(*(uint32_t*)(copydata + 5));

    crom->compressed = malloc(coded_bytes);
    sread(fp, crom->compressed, coded_bytes);

    tag = read16be(fp);
    if (tag != 0xffb2)
        warn("Expected tag 0xffb2, got 0x%x instead\n", tag);
    uint16_t litptr_len = read16be(fp);
    if (litptr_len != 6)
        warn("Expected tag length 6, got %d instead\n", litptr_len);
    uint32_t literal_len;
    sread(fp, &literal_len, litptr_len - 2);
    literal_len = ntohl(literal_len);

    crom->literaldata = malloc(literal_len);
    sread(fp, crom->literaldata, literal_len);
}

void unpack_huffman_table(int index, huff_entry_t table[], uint8_t **srcptr) {
    // These are identical to JPEG DHTs: 16 counts, one for each codeword length, followed by symbols.
    uint8_t id = *(*srcptr)++;
    if (id != 0xf0 + index)
        warn("Expected Huffman table ID 0x%x, found %x\n", 0xf0 + index, id);

    uint8_t counts[16];
    memcpy(counts, *srcptr, 16);
    *srcptr += 16;

    uint16_t code = 0;

    for (int length=1; length<=16; length++) {
        int count = counts[length-1];

        for (int sym_index=0; sym_index<count; sym_index++) {
            uint8_t symbol = *(*srcptr)++;
            huff_entry_t entry = (length << 8) | symbol;

            // write all table entries whose index starts with this code
            int index = code << (16-length);
            int num_entries = 0x10000 >> length;
            for (int i=0; i<num_entries; i++) {
                table[index++] = entry;
            }

            code += 1;
        }

        code <<= 1;
    }
}

void unpack_huffman_tables(struct crom *crom) {
    uint8_t *huffptr = crom->huffdata;
    for (int i=0; i<3; i++) {
        unpack_huffman_table(i, crom->hufftable[i], &huffptr);
    }
}

void decompress_one_symbol(struct decomp_state *decomp, huff_entry_t table[]) {
    while (decomp->buffer_bits < 16) {
        decomp->buffer <<= 8;
        decomp->buffer |= *decomp->in++;
        decomp->buffer_bits += 8;
    }

    uint16_t index = decomp->buffer >> (decomp->buffer_bits - 16);
    huff_entry_t entry = table[index];

    if (!entry)
        die("ERROR: invalid prefix code in compressed data!\n");

    int codeword_len = entry >> 8;
    decomp->buffer_bits -= codeword_len;

    uint8_t symbol = entry & 0xff;
    *decomp->out++ = symbol;
}

void decompress_items(struct crom *crom) {
    struct decomp_state *decomp = &crom->decompressor;

    decomp->in = crom->compressed;
    decomp->buffer_bits = 0;

    crom->items = malloc(crom->num_items * 3);
    decomp->out = crom->items;

    for (int i=0; i<crom->num_items; i++) {
        for (int table=0; table<3; table++)
            decompress_one_symbol(decomp, crom->hufftable[table]);
    }
}

void execute_items(struct crom *crom, FILE *outfp) {
    uint8_t *ptr = crom->items;
    uint8_t *literal_ptr = crom->literaldata;

    size_t out_bytes = 0;
    size_t out_buf_size = 128;
    uint8_t *out_buf = malloc(out_buf_size);

    for (int i=0; i<crom->num_items; i++) {
        uint8_t control = *ptr++;
        uint16_t offset = *ptr++;
        offset |= *ptr++ << 8;

        if (out_bytes + control + 2 > out_buf_size) {
            out_buf = realloc(out_buf, 2*out_buf_size);
            out_buf_size *= 2;
        }

        if (!offset) {
            if (control == 0xff)
                continue;
            uint8_t literals = control + 1;
            memcpy(out_buf + out_bytes, literal_ptr, literals);
            literal_ptr += literals;
            out_bytes += literals;
        } else {
            uint8_t copy = control + 2;
            // these can self-overlap, so we can't use memcpy/memmove
            while (copy--) {
                out_buf[out_bytes] = out_buf[out_bytes - offset];
                out_bytes++;
            }
        }
    }

    fwrite(out_buf, out_bytes, 1, outfp);
    free(out_buf);
}

void uncrom(FILE *infp, FILE *outfp) {
    struct crom *crom = malloc(sizeof(struct crom));
    memset(crom, 0, sizeof(struct crom));

    read_crom_data(infp, crom);
    unpack_huffman_tables(crom);
    decompress_items(crom);
    execute_items(crom, outfp);
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s cromfile.crom\n", argv[0]);
        exit(1);
    }

    FILE *fp = fopen(argv[1], "rb");
    if (!fp)
        die("ERROR: could not open '%s'\n", argv[1]);

    char magic[4];
    sread(fp, magic, 4);

    if (memcmp(magic, "CROM", 4))
        die("ERROR: missing CROM magic at start of file\n");

    int segment = 0;
    while (1) {
        char *filename;
        if (asprintf(&filename, "%s.%d.bin", argv[1], segment++) < 0)
            die("Out of memory");

        FILE *outfp = fopen(filename, "wb");
        if (!outfp)
            die("ERROR: could not open '%s' for writing\n", filename);

        uncrom(fp, outfp);

        // check for EOF
        uint8_t junk;
        int nread = fread(&junk, 1, 1, fp);
        if (nread < 1)
            break;
        fseek(fp, -1, SEEK_CUR);
    }

    return 0;
}
