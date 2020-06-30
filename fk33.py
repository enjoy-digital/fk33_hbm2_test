#!/usr/bin/env python3

# This file is Copyright (c) 2020 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import os
import argparse

from migen import *

from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform, VivadoProgrammer

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser

from litepcie.phy.usppciephy import USPPCIEPHY
from litepcie.core import LitePCIeEndpoint, LitePCIeMSI
from litepcie.frontend.dma import LitePCIeDMA
from litepcie.frontend.wishbone import LitePCIeWishboneBridge
from litepcie.software import generate_litepcie_software

# Use ----------------------------------------------------------------------------------------------

# Build and load bitstream:
# -------------------------
# ./fk33.py --driver --build --load
# reboot computer

# Build and load kernel module:
# -----------------------------
# cd build/fk33/driver/kernel
# make clean all
# sudo ./init.sh
#
# Create bridge:
# --------------
# sudo litex_server --pcie --pcie-bar=/sys/bus/pci/devices/0000\:01\:00.0/resource0

# Use scripts:
# -------------
# ./test_regs.py
# ./litescope_wishbone.py (--help to see preconfigured triggers)

# Use console:
# litex_crossover_uart --base-address=-0x82000000 (will create /dev/pts/X)
# litex_term /dev/ptsX

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

    ("i2c",
        Subsignal("scl", Pins("BB24"), IOStandard("LVCMOS18"), Misc("DRIVE=8")),
        Subsignal("sda", Pins("BA24"), IOStandard("LVCMOS18"), Misc("DRIVE=8")),
    ),

    ("pcie_x4", 0,
        Subsignal("rst_n", Pins("BE24"), IOStandard("LVCMOS18")),
        Subsignal("clk_p", Pins("AD9")),
        Subsignal("clk_n", Pins("AD8")),
        Subsignal("rx_p",  Pins("AL2 AM4 AK4 AN2")),
        Subsignal("rx_n",  Pins("AL1 AM3 AK3 AN1")),
        Subsignal("tx_p",  Pins("Y5  AA7 AB5 AC7")),
        Subsignal("tx_n",  Pins("Y4  AA6 AB4 AC6")),
    ),
]

# Platform -----------------------------------------------------------------------------------------

class Platform(XilinxPlatform):
    default_clk_name   = "clk200"
    default_clk_period = 1e9/200e6

    def __init__(self):
        XilinxPlatform.__init__(self, "xcvu33p-fsvh2104-2L-e-es1", _io, toolchain="vivado")

    def create_programmer(self):
        return VivadoProgrammer()

    def do_finalize(self, fragment):
        XilinxPlatform.do_finalize(self, fragment)
        self.add_period_constraint(self.lookup_request("clk200", loose=True), 1e9/200e6)
        # Shutdown on overheatng
        self.add_platform_command("set_property BITSTREAM.CONFIG.OVERTEMPSHUTDOWN ENABLE [current_design]")
        # Reduce programming time
        self.add_platform_command("set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]")

# CRG ----------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_sys    = ClockDomain()

        # # #

        self.submodules.pll = pll = USMMCM(speedgrade=-2)
        pll.register_clkin(platform.request("clk200"), 200e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(125e6), **kwargs):
        platform = Platform()

        kwargs["uart_name"]      = "crossover"
        kwargs["csr_data_width"] = 32

        # SoCCore ----------------------------------------------------------------------------------
        SoCCore.__init__(self, platform, sys_clk_freq,
            ident          = "LiteX SoC on Forest Kitten 33",
            ident_version  = True,
            **kwargs)

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # PCIe -------------------------------------------------------------------------------------
        # PHY
        self.submodules.pcie_phy = USPPCIEPHY(platform, platform.request("pcie_x4"),
            data_width = 128,
            bar0_size  = 0x20000
        )
        #self.pcie_phy.add_timing_constraints(platform) # FIXME
        platform.add_false_path_constraints(self.crg.cd_sys.clk, self.pcie_phy.cd_pcie.clk)
        self.add_csr("pcie_phy")

        # Endpoint
        self.submodules.pcie_endpoint = LitePCIeEndpoint(self.pcie_phy,
            endianness           = "little",
            max_pending_requests = 8
        )

        # Wishbone bridge
        self.submodules.pcie_bridge = LitePCIeWishboneBridge(self.pcie_endpoint,
            base_address = self.mem_map["csr"])
        self.add_wb_master(self.pcie_bridge.wishbone)

        # DMA0
        self.submodules.pcie_dma0 = LitePCIeDMA(self.pcie_phy, self.pcie_endpoint,
            with_buffering = True, buffering_depth=1024,
            with_loopback  = True)
        self.add_csr("pcie_dma0")

        self.add_constant("DMA_CHANNELS", 1)

        # MSI
        self.submodules.pcie_msi = LitePCIeMSI()
        self.add_csr("pcie_msi")
        self.comb += self.pcie_msi.source.connect(self.pcie_phy.msi)
        self.interrupts = {
            "PCIE_DMA0_WRITER":    self.pcie_dma0.writer.irq,
            "PCIE_DMA0_READER":    self.pcie_dma0.reader.irq,
        }
        for i, (k, v) in enumerate(sorted(self.interrupts.items())):
            self.comb += self.pcie_msi.irqs[i].eq(v)
            self.add_constant(k + "_INTERRUPT", i)

        # Leds -------------------------------------------------------------------------------------
        self.submodules.leds = LedChaser(
            pads         = Cat(*[platform.request("user_led", i) for i in range(7)]),
            sys_clk_freq = sys_clk_freq)
        self.add_csr("leds")

        # Analyzer ---------------------------------------------------------------------------------
        from litescope import LiteScopeAnalyzer
        analyzer_signals = [
            self.cpu.reset,
            self.cpu.periph_buses[0],
            self.cpu.periph_buses[1],
        ]
        self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals,
            depth        = 2048,
            clock_domain = "sys",
            csr_csv      = "analyzer.csv")
        self.add_csr("analyzer")

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on Forest Kitten 33")
    parser.add_argument("--build",  action="store_true", help="Build bitstream")
    parser.add_argument("--driver", action="store_true", help="Generate LitePCIe driver")
    parser.add_argument("--load",   action="store_true", help="Load bitstream")
    soc_core_args(parser)
    args = parser.parse_args()

    soc = BaseSoC(**soc_core_argdict(args))
    builder = Builder(soc, output_dir="build/fk33", csr_csv="csr.csv")
    builder.build(run=args.build)

    if args.driver:
        generate_litepcie_software(soc, os.path.join(builder.output_dir, "driver"))

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + ".bit"))

if __name__ == "__main__":
    main()
