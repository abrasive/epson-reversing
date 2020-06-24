# Library routines for .rcx files.

from collections import defaultdict

def parse_rcx(data):
    """Parse an RCX header.

    Returns:
        length: total length of the header, including terminating byte
        cfg: nested dicts containing the INI-style information in the header
    """
    header_end = data.index(b'\f') + 1

    header = data[:header_end-1].decode('ascii')
    lines = header.split('\r\n')
    assert lines[0] == 'RCX'
    assert lines[1] == 'SEIKO EPSON EpsonNet Form'

    cur_section = ''
    cfg = defaultdict(dict)

    for line in lines[2:]:
        if line.startswith('[') and line.endswith(']'):
            cur_section = line[1:-1]
            continue

        if line.strip() == '':
            continue

        key, value = line.split('=', 1)
        assert value.startswith('"') and value.endswith('"')

        cfg[cur_section][key] = value[1:-1]

    return header_end, cfg
