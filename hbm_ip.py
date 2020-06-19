import os

from migen import *

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.interconnect.axi import *


class HBMIP(Module, AutoCSR):
    """Xilinx Virtex US+ High Bandwidth Memory IP wrapper"""
    def __init__(self, platform, hbm_ip_name="hbm_0"):
        self.platform = platform
        self.hbm_name = hbm_ip_name

        self.axi = []
        self.apb = []
        self.apb_complete = []
        # TODO: use it to disable AXI/APB
        self.dram_stat_cattrip = []  # high when temp > 120C, disable memory access if it happens
        self.dram_stat_temp = []  # temp in degree Celsius

        self.hbm_params = params = {}

        rst = Signal(reset=1)

        # Clocks -----------------------------------------------------------------------------------
        # ref = 100 MHz (HBM: 900 (225-900) MHz)
        # drives internal PLL (1 per stack)
        for i in range(2):
            params[f"i_HBM_REF_CLK_{i:1d}"] = ClockSignal("hbm_ref")

        # APB: 100 (50-100) MHz
        for i in range(2):
            params[f"i_APB_{i:1d}_PCLK"] = ClockSignal("apb")
            params[f"i_APB_{i:1d}_PRESET_N"] = ~(ResetSignal("apb") | rst)

        # AXI: 450 (225-450) MHz
        for i in range(32):
            params[f"i_AXI_{i:02d}_ACLK"] = ClockSignal("axi")
            params[f"i_AXI_{i:02d}_ARESET_N"] = ~(ResetSignal("apb") | rst)

        # AXI --------------------------------------------------------------------------------------
        for i in range(32):
            axi = AXIInterface(data_width=256, address_width=37, id_width=6)
            self.axi.append(axi)

            # ax_description()
            # master -> slave
            params[f"i_AXI_{i:02d}_AWADDR"]       = axi.aw.addr
            params[f"i_AXI_{i:02d}_AWBURST"]      = axi.aw.burst
            params[f"i_AXI_{i:02d}_AWID"]         = axi.aw.id
            params[f"i_AXI_{i:02d}_AWLEN"]        = axi.aw.len
            params[f"i_AXI_{i:02d}_AWSIZE"]       = axi.aw.size
            params[f"i_AXI_{i:02d}_AWVALID"]      = axi.aw.valid
            params[f"i_AXI_{i:02d}_ARADDR"]       = axi.ar.addr
            params[f"i_AXI_{i:02d}_ARBURST"]      = axi.ar.burst
            params[f"i_AXI_{i:02d}_ARID"]         = axi.ar.id
            params[f"i_AXI_{i:02d}_ARLEN"]        = axi.ar.len
            params[f"i_AXI_{i:02d}_ARSIZE"]       = axi.ar.size
            params[f"i_AXI_{i:02d}_ARVALID"]      = axi.ar.valid
            # slave -> master
            params[f"o_AXI_{i:02d}_AWREADY"]      = axi.aw.ready
            params[f"o_AXI_{i:02d}_ARREADY"]      = axi.ar.ready
            # w_description()
            # master -> slave
            params[f"i_AXI_{i:02d}_WDATA"]        = axi.w.data
            params[f"i_AXI_{i:02d}_WLAST"]        = axi.w.last
            params[f"i_AXI_{i:02d}_WSTRB"]        = axi.w.strb
            params[f"i_AXI_{i:02d}_WDATA_PARITY"] = Constant(0)  # w=32 FIXME
            params[f"i_AXI_{i:02d}_WVALID"]       = axi.w.valid
            # slave -> master
            params[f"o_AXI_{i:02d}_WREADY"]       = axi.w.ready
            # b_description()
            # master -> slave
            params[f"i_AXI_{i:02d}_BREADY"]       = axi.b.ready
            # slave -> master
            params[f"o_AXI_{i:02d}_BID"]          = axi.b.id
            params[f"o_AXI_{i:02d}_BRESP"]        = axi.b.resp
            params[f"o_AXI_{i:02d}_BVALID"]       = axi.b.valid
            # r_description()
            # master -> slave
            params[f"i_AXI_{i:02d}_RREADY"]       = axi.r.ready
            # slave -> master
            params[f"o_AXI_{i:02d}_RDATA_PARITY"] = Signal(32)  # FIXME
            params[f"o_AXI_{i:02d}_RDATA"]        = axi.r.data
            params[f"o_AXI_{i:02d}_RID"]          = axi.r.id
            params[f"o_AXI_{i:02d}_RLAST"]        = axi.r.last
            params[f"o_AXI_{i:02d}_RRESP"]        = axi.r.resp
            params[f"o_AXI_{i:02d}_RVALID"]       = axi.r.valid

        # APB --------------------------------------------------------------------------------------
        # FIXME: wb <-> apb
        for i in range(2):
            params[f"i_APB_{i:1d}_PWDATA"]  = Constant(0)  # w=32
            params[f"i_APB_{i:1d}_PADDR"]   = Constant(0)  # w=22
            params[f"i_APB_{i:1d}_PENABLE"] = Constant(0)
            params[f"i_APB_{i:1d}_PSEL"]    = Constant(0)
            params[f"i_APB_{i:1d}_PWRITE"]  = Constant(0)

            params[f"o_APB_{i:1d}_PRDATA"]  = Signal(32)
            params[f"o_APB_{i:1d}_PREADY"]  = Signal()
            params[f"o_APB_{i:1d}_PSLVERR"] = Signal()

            params[f"o_apb_complete_{i:1d}"] = apb_complete = Signal()
            self.apb_complete.append(apb_complete)

        # Temperature ------------------------------------------------------------------------------
        for i in range(2):
            params[f"o_DRAM_{i:1d}_STAT_CATTRIP"] = stat_cattrip = Signal()
            params[f"o_DRAM_{i:1d}_STAT_TEMP"]    = stat_temp    = Signal(7)
            self.dram_stat_cattrip.append(stat_cattrip)
            self.dram_stat_temp.append(stat_temp)

        # CSRs -------------------------------------------------------------------------------------
        self.init_done = CSRStatus()
        self.comb += self.init_done.status.eq(self.apb_complete[0] & self.apb_complete[1])
        self.apb_done = CSRStatus(2)
        self.comb += self.apb_done.status.eq(Cat(self.apb_complete[0], self.apb_complete[1]))

        self.clk_resets = CSRStatus(fields=[
            CSRField("apb", 1),
            CSRField("axi", 1),
        ])
        self.comb += self.clk_resets.fields.apb.eq(~ResetSignal("apb"))
        self.comb += self.clk_resets.fields.axi.eq(~ResetSignal("axi"))

        self.csr_cattrip_0 = CSRStatus(1, name="dram_stat_cattrip_0")
        self.csr_cattrip_1 = CSRStatus(1, name="dram_stat_cattrip_1")
        self.csr_temp_0 = CSRStatus(7, name="dram_stat_temp_0")
        self.csr_temp_1 = CSRStatus(7, name="dram_stat_temp_1")
        self.comb += [
            self.csr_cattrip_0.status.eq(self.dram_stat_cattrip[0]),
            self.csr_cattrip_1.status.eq(self.dram_stat_cattrip[1]),
            self.csr_temp_0.status.eq(self.dram_stat_temp[0]),
            self.csr_temp_1.status.eq(self.dram_stat_temp[1]),
        ]

        self.rst_toggle = CSR()
        self.sync += If(self.rst_toggle.re, rst.eq(~rst))

    def add_sources(self, platform):
        this_dir = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
        platform.add_ip(os.path.join(this_dir, "ip", "hbm", self.hbm_name + ".xci"))
        # FIXME: in Vivado 2018 it is not possible to disable XSDB so we need a debug core?
        #  platform.add_ip(os.path.join(this_dir, "ip", "ila", "ila_0.xci"))

    def do_finalize(self):
        self.add_sources(self.platform)
        self.specials += Instance(self.hbm_name, **self.hbm_params)
        #  self.specials += Instance("ila_0",  # FIXME: remove
        #                            i_clk=ClockSignal("apb"),
        #                            i_probe0=self.axi[0].ar.valid,
        #                            )
