#!/usr/bin/env python3

import sys
import argparse

from litex import RemoteClient
from litescope import LiteScopeAnalyzerDriver

parser = argparse.ArgumentParser()
parser.add_argument("--cpu_reset",  action="store_true", help="Trigger on CPU Reset.")
parser.add_argument("--ibus_stb",   action="store_true", help="Trigger on Ibus stb rising edge.")
parser.add_argument("--ibus_dat_w", default=0,           help="Trigger on Ibus dat_w value.")
parser.add_argument("--dbus_stb",   action="store_true", help="Trigger on Ibus stb risign edge.")
parser.add_argument("--dbus_dat_w", default=0,           help="Trigger on Ibus dat_w value.")
parser.add_argument("--offset",     default=128,         help="Capture Offset.")
parser.add_argument("--length",     default=2048,        help="Capture Length.")
args = parser.parse_args()

wb = RemoteClient(base_address=-0x82000000)
wb.open()

# # #

analyzer = LiteScopeAnalyzerDriver(wb.regs, "analyzer", debug=True)
analyzer.configure_group(0)
if args.cpu_reset:
	analyzer.add_rising_edge_trigger("basesoc_cpu_reset")
elif args.ibus_stb:
	analyzer.add_rising_edge_trigger("basesoc_cpu_ibus_stb")
elif args.ibus_dat_w:
	analyzer.configure_trigger(cond={"basesoc_cpu_ibus_dat_w": int(args.ibus_dat_w, 0)})
elif args.dbus_stb:
	analyzer.add_rising_edge_trigger("basesoc_cpu_dbus_stb")
elif args.dbus_dat_w:
	analyzer.configure_trigger(cond={"basesoc_cpu_dbus_dat_w": int(args.dbus_dat_w, 0)})
else:
    analyzer.configure_trigger(cond={})
analyzer.run(offset=int(args.offset), length=int(args.length))

analyzer.wait_done()
analyzer.upload()
analyzer.save("dump.vcd")

# # #

wb.close()