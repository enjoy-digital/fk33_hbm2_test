#!/usr/bin/env python3

import sys

from litex import RemoteClient

wb = RemoteClient(base_address=-0x82000000)
wb.open()

# # #

print("FPGA identifier:", end="")
identifier = ""
for i in range(256):
    c = chr(wb.read(wb.bases.identifier_mem + 4*i) & 0xff)
    identifier += c
    if c == "\0":
        break
print(identifier)

print("Dump register values:")
for name, reg in wb.regs.__dict__.items():
    show = True
    if len(sys.argv) > 1:
        show = sys.argv[1] in name
    if show:
        print("0x{:08x} : 0x{:08x} {}".format(reg.addr, reg.read(), name))

# # #

wb.close()
