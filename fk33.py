#!/usr/bin/env python3

# This file is Copyright (c) 2020-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import os
import argparse

from migen import *

from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform, VivadoProgrammer

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.soc import SoCRegion
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser

from hbm_ip import HBMIP

from litedram.common import *
from litedram.frontend.axi import *

from litescope import LiteScopeAnalyzer

# Use ----------------------------------------------------------------------------------------------

# Build and load bitstream:
# -------------------------
# ./fk33.py  --build --load (Tested with Vivado 2018.2)

# Create bridge:
# --------------
# litex_server --jtag --jtag-config=openocd_xc7_ft2232.cfg --jtag-chain=2

# Use:
# ----
# Dump regs:    litex_cli --regs
# Use analyzer: litescope_cli
# Use console:  litex_term bridge

# Verify HBM in the BIOS:
# mem_list
# mem_test  0x40000000 0x100000
# mem_speed 0x40000000 0x100000

# IOs ----------------------------------------------------------------------------------------------

_io = [
    ("clk200", 0,
        Subsignal("p", Pins("BC26"), IOStandard("LVDS")),
        Subsignal("n", Pins("BC27"), IOStandard("LVDS"))
    ),

    ("user_led", 0, Pins("BD25"), IOStandard("LVCMOS18")),
    ("user_led", 1, Pins("BE26"), IOStandard("LVCMOS18")),
    ("user_led", 2, Pins("BD23"), IOStandard("LVCMOS18")),
    ("user_led", 3, Pins("BF26"), IOStandard("LVCMOS18")),
    ("user_led", 4, Pins("BC25"), IOStandard("LVCMOS18")),
    ("user_led", 5, Pins("BB26"), IOStandard("LVCMOS18")),
    ("user_led", 6, Pins("BB25"), IOStandard("LVCMOS18")),
]

# Platform -----------------------------------------------------------------------------------------

class Platform(XilinxPlatform):
    default_clk_name   = "clk200"
    default_clk_period = 1e9/200e6

    def __init__(self):
        XilinxPlatform.__init__(self, "xcvu33p-fsvh2104-2L-e", _io, toolchain="vivado")

    def create_programmer(self):
        return VivadoProgrammer()

    def do_finalize(self, fragment):
        XilinxPlatform.do_finalize(self, fragment)
        self.add_period_constraint(self.lookup_request("clk200", loose=True), 1e9/200e6)
        # Shutdown on overheatng
        self.add_platform_command("set_property BITSTREAM.CONFIG.OVERTEMPSHUTDOWN ENABLE [current_design]")
        # Reduce programming time
        self.add_platform_command("set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]")
        self.add_platform_command("connect_debug_port dbg_hub/clk [get_nets apb_clk]")

# CRG ----------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        assert 225e6 <= sys_clk_freq <= 450e6
        self.clock_domains.cd_sys     = ClockDomain()
        self.clock_domains.cd_hbm_ref = ClockDomain()
        self.clock_domains.cd_apb     = ClockDomain()

        # # #

        self.submodules.pll = pll = USMMCM(speedgrade=-2)
        pll.register_clkin(platform.request("clk200"), 200e6)
        pll.create_clkout(self.cd_sys,     sys_clk_freq)
        pll.create_clkout(self.cd_hbm_ref, 100e6)
        pll.create_clkout(self.cd_apb,     100e6)
        platform.add_false_path_constraints(self.cd_sys.clk, self.cd_apb.clk)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(250e6), with_hbm=False, with_analyzer=False, **kwargs):
        platform = Platform()

        # SoCCore ----------------------------------------------------------------------------------
        kwargs["uart_name"] = "crossover"
        SoCCore.__init__(self, platform, sys_clk_freq,
            ident          = "LiteX HBM2 Test SoC on Forest Kitten 33.",
            ident_version  = True,
            **kwargs)

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # JTAGBone --------------------------------------------------------------------------------
        self.add_jtagbone(chain=2) # Chain 1 already used by HBM2 debug probes.

        # Leds -------------------------------------------------------------------------------------
        self.submodules.leds = LedChaser(
            pads         = Cat(*[platform.request("user_led", i) for i in range(7)]),
            sys_clk_freq = sys_clk_freq)

        # HBM --------------------------------------------------------------------------------------
        if with_hbm:
            # Add HBM Core.
            self.submodules.hbm = hbm = ClockDomainsRenamer({"axi": "sys"})(HBMIP(platform))

            # Connect four of the HBM's AXI interfaces to the main bus of the SoC.
            for i in range(4):
                axi_hbm      = hbm.axi[i]
                axi_lite_hbm = AXILiteInterface(data_width=256, address_width=33)
                self.submodules += AXILite2AXI(axi_lite_hbm, axi_hbm)
                self.bus.add_slave(f"hbm{i}", axi_lite_hbm, SoCRegion(origin=0x4000_0000 + 0x1000_0000*i, size=0x1000_0000)) # 256MB.

        # Analyzer ---------------------------------------------------------------------------------
        if with_analyzer:
            analyzer_signals = [
                Signal(2), # Add useful signals.
                Signal(2), # Add useful signals.
            ]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals,
                depth        = 2048,
                clock_domain = "sys",
                csr_csv      = "analyzer.csv"
            )

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX HBM2 Test SoC on Forest Kitten 33.")
    parser.add_argument("--build",         action="store_true", help="Build bitstream.")
    parser.add_argument("--load",          action="store_true", help="Load bitstream.")
    parser.add_argument("--with-hbm",      action="store_true", help="Use HBM.")
    parser.add_argument("--with-analyzer", action="store_true", help="Enable Analyzer.")
    soc_core_args(parser)
    args = parser.parse_args()

    soc = BaseSoC(with_hbm=args.with_hbm, with_analyzer=args.with_analyzer, **soc_core_argdict(args))
    builder = Builder(soc, output_dir="build/fk33", csr_csv="csr.csv")
    builder.build(run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + ".bit"))

if __name__ == "__main__":
    main()
