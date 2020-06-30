#!/usr/bin/env python3

# This file is Copyright (c) 2020 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import os
import argparse
from math import log2

from migen import *

from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform, VivadoProgrammer

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.integration.soc import SoCRegion
from litex.soc.cores.led import LedChaser
from litex.soc.interconnect import wishbone, axi
from litex.soc.interconnect.axi import AXILiteInterface

from hbm_ip import HBMIP
from debug import BusCSRDebug, WishboneSoftControl, WishboneGuard, WishboneSoftInjector
from axil2axi import AXILite2AXI
import wb2axi

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

# CRG ----------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_sys     = ClockDomain()
        self.clock_domains.cd_hbm_ref = ClockDomain()
        self.clock_domains.cd_apb     = ClockDomain()

        # # #

        self.submodules.pll = pll = USMMCM(speedgrade=-2)
        pll.register_clkin(platform.request("clk200"), 200e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)
        pll.create_clkout(self.cd_hbm_ref, 100e6)
        pll.create_clkout(self.cd_apb, 100e6)
        assert 225e6 <= sys_clk_freq <= 450e6

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(450e6), with_hbm=False, with_full_wb2axi=False, debug=False,
                 **kwargs):
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

        # HBM --------------------------------------------------------------------------------------
        if with_hbm:
            hbm = HBMIP(platform)
            self.submodules.hbm = ClockDomainsRenamer({"axi": "sys"})(hbm)
            self.add_csr("hbm")
            axi_hbm = hbm.axi[0]

            # Add main_ram wishbone
            wb_cpu = wishbone.Interface()
            self.bus.add_region("main_ram", SoCRegion(
                origin=self.mem_map["main_ram"],
                size=kwargs.get("max_sdram_size", 0x40000000)  # 1GB; could be 8GB with wider address
            ))
            self.bus.add_slave("main_ram", wb_cpu)

            # Count timeouts on main_ram bus and disconnect it when we exceed limit
            self.submodules.wb_guard = WishboneGuard(wb_cpu)
            self.add_csr("wb_guard")
            wb_cpu = self.wb_guard.slave

            # Option to switch between software/hardware control
            wb_soft = wishbone.Interface.like(wb_cpu)
            self.submodules.wb_softcontrol = WishboneSoftControl(wb_soft)
            self.add_csr("wb_softcontrol")
            self.submodules.wb_injector = WishboneSoftInjector(wb_cpu, wb_soft)
            self.add_csr("wb_injector")
            wb_cpu = self.wb_injector.wb_slave

            # Make sure to use 256-bit wishbone so that  we have correct AxSIZE=0b101 (356-bit)
            # Insert L2 cache or use only part of CPU bus data width
            wb_hbm = wishbone.Interface(data_width=axi_hbm.data_width)
            l2_size = kwargs.get("l2_size", 8192)
            if l2_size != 0:
                print("=" * 80)
                print("  Adding L2 cache of size = %d" % l2_size)
                print("=" * 80)
                self.add_l2_cache(wb_cpu, wb_hbm,
                                  l2_cache_size           = l2_size,
                                  l2_cache_min_data_width = kwargs.get("min_l2_data_width", 128))
            else:
                print("=" * 80)
                print("  Using %d bits of data path" % wb_cpu.data_width)
                print("=" * 80)
                self.comb += wb_cpu.connect(wb_hbm)

            # If L2 cache is present, it will shift the address, so we need to use a shifted origin
            origin = self.mem_map["main_ram"] // (wb_hbm.data_width // wb_cpu.data_width)

            if not with_full_wb2axi:
                # Use native Litex primities to convert from wishbone to AXI
                # wb_cpu -> (l2 cache) -> wb_hbm -> wb_wider -> (wb2axilite) -> axi_lite_hbm -> axi_hbm
                print("  Using Wishbone2AXILite")
                print("=" * 80)

                wb_wider = wishbone.Interface(data_width=wb_hbm.data_width, adr_width=37 - 5)
                self.comb += wb_hbm.connect(wb_wider)

                axi_lite_hbm = axi.AXILiteInterface(data_width=axi_hbm.data_width,
                                                    address_width=axi_hbm.address_width)
                self.submodules.wb2axi = axi.Wishbone2AXILite(wb_wider, axi_lite_hbm,
                                                              base_address=origin)

                # Fixed burst is not supported by AXI HBM IP
                self.submodules.axil2axi = AXILite2AXI(axi_lite_hbm, axi_hbm, burst_type="INCR")
            else:
                # Use pipelined wishbone to AXI
                # wb_cpu -> (l2 cache) -> wb_hbm -> (wbc2pipe) -> wb_pipe -> (wb2axi) -> axi_hbm
                print("  Using wb->wbp->axi")
                print("=" * 80)

                wb_wider = wishbone.Interface(data_width=wb_hbm.data_width, adr_width=37 - 5)
                self.comb += wb_hbm.connect(wb_wider)

                wb_pipe = wb2axi.WishbonePipelined(data_width=256, adr_width=32)
                self.submodules.wbc2wbp = wb2axi.WishboneClassic2Pipeline(wb_wider, wb_pipe)
                self.wbc2wbp.add_sources(platform)

                self.submodules.wb2axi = wb2axi.WishbonePipelined2AXI(wb_pipe, axi_hbm,
                                                                      base_address=origin)
                self.wb2axi.add_sources(platform)

            if debug:
                print("  Adding debug CSRs")
                print("=" * 80)
                self.add_bus_debug_csrs(wb_cpu, wb_hbm, axi_hbm)

    def do_finalize(self):
        super().do_finalize()
        self.comb += self.wb_guard.timeout.eq(self.bus_interconnect.timeout.error)

    def add_l2_cache(self, wb_master, wb_slave,
                    l2_cache_size           = 8192,
                    l2_cache_min_data_width = 128,
                    l2_cache_reverse        = True,
                    l2_cache_full_memory_we = True):

        assert wb_slave.data_width >= l2_cache_min_data_width

        l2_cache_size = max(l2_cache_size, int(2*wb_slave.data_width/8)) # Use minimal size if lower
        l2_cache_size = 2**int(log2(l2_cache_size))                  # Round to nearest power of 2
        l2_cache            = wishbone.Cache(
            cachesize = l2_cache_size//4,
            master    = wb_master,
            slave     = wb_slave,
            reverse   = l2_cache_reverse)

        if l2_cache_full_memory_we:
            l2_cache = FullMemoryWE()(l2_cache)

        self.submodules.l2_cache = l2_cache
        self.add_config("L2_SIZE", l2_cache_size)

    def add_bus_debug_csrs(self, wb_cpu, wb_hbm, axi):
        class Debug(Module, AutoCSR):
            def __init__(self):
                self.reset = CSR()
                self.submodules.wb_cpu = BusCSRDebug(
                    description = {
                        "adr": wb_cpu.adr,
                        "dat_w": wb_cpu.dat_w,
                        "dat_r": wb_cpu.dat_r,
                        "sel": wb_cpu.sel,
                        "we": wb_cpu.we,
                        "err": wb_cpu.err,
                    },
                    trigger = wb_cpu.stb & wb_cpu.cyc & (wb_cpu.ack | wb_cpu.err),
                    reset = self.reset.re,
                )
                self.submodules.wb_hbm = BusCSRDebug(
                    description = {
                        "adr": wb_hbm.adr,
                        "dat_w": wb_hbm.dat_w,
                        "dat_r": wb_hbm.dat_r,
                        "sel": wb_hbm.sel,
                        "we": wb_hbm.we,
                        "err": wb_hbm.err,
                    },
                    trigger = wb_hbm.stb & wb_hbm.cyc & (wb_hbm.ack | wb_hbm.err),
                    reset = self.reset.re,
                )
                self.submodules.axi_aw = BusCSRDebug(
                    description = {
                        "addr": axi.aw.addr,
                        #  "burst": axi.aw.burst,
                        #  "len": axi.aw.len,
                        #  "size": axi.aw.size,
                        #  "id": axi.aw.id,
                    },
                    trigger = axi.aw.valid & axi.aw.ready,
                    reset = self.reset.re,
                )
                self.submodules.axi_w = BusCSRDebug(
                    description = {
                        "data": axi.w.data,
                        "strb": axi.w.strb,
                        #  "id": axi.w.id,
                    },
                    trigger = axi.w.valid & axi.w.ready,
                    reset = self.reset.re,
                )
                self.submodules.axi_b = BusCSRDebug(
                    description = {
                        "resp": axi.b.resp,
                        #  "id": axi.b.id,
                    },
                    trigger = axi.b.valid & axi.b.ready,
                    reset = self.reset.re,
                )
                self.submodules.axi_ar = BusCSRDebug(
                    description = {
                        "addr": axi.ar.addr,
                        #  "burst": axi.ar.burst,
                        #  "len": axi.ar.len,
                        #  "size": axi.ar.size,
                        #  "id": axi.ar.id,
                    },
                    trigger = axi.ar.valid & axi.ar.ready,
                    reset = self.reset.re,
                )
                self.submodules.axi_r = BusCSRDebug(
                    description = {
                        "resp": axi.r.resp,
                        "data": axi.r.data,
                        #  "id": axi.r.id,
                    },
                    trigger = axi.r.valid & axi.r.ready,
                    reset = self.reset.re,
                )
        self.submodules.debug = Debug()
        self.add_csr("debug")

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on Forest Kitten 33")
    parser.add_argument("--build",    action="store_true", help="Build bitstream")
    parser.add_argument("--driver",   action="store_true", help="Generate LitePCIe driver")
    parser.add_argument("--load",     action="store_true", help="Load bitstream")
    parser.add_argument("--with-hbm", action="store_true", help="Use HBM")
    parser.add_argument("--with-full-wb2axi", action="store_true", help="Use full Wishbone2AXI")
    parser.add_argument("--l2-size",  type=int,            help="Set HBM L2 cache size")
    parser.add_argument("--ndebug",   action="store_true", help="Set HBM L2 cache size")
    builder_args(parser)
    soc_core_args(parser)
    args = parser.parse_args()

    kwargs = soc_core_argdict(args)
    if args.l2_size is not None:
        kwargs["l2_size"] = args.l2_size
    soc = BaseSoC(with_hbm=args.with_hbm, with_full_wb2axi=args.with_full_wb2axi, debug=not args.ndebug, **kwargs)
    builder = Builder(soc, output_dir="build/fk33", csr_csv="csr.csv")
    builder.build(run=args.build)

    if args.driver:
        generate_litepcie_software(soc, os.path.join(builder.output_dir, "driver"))

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + ".bit"))

if __name__ == "__main__":
    main()
