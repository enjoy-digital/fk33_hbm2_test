import os
import copy

from migen import *


wb_pipelined_layout = [
    ("adr",    "adr_width", DIR_M_TO_S),
    ("dat_w", "data_width", DIR_M_TO_S),
    ("dat_r", "data_width", DIR_S_TO_M),
    ("sel",    "sel_width", DIR_M_TO_S),
    ("cyc",              1, DIR_M_TO_S),
    ("stall",            1, DIR_M_TO_S),
    ("stb",              1, DIR_M_TO_S),
    ("ack",              1, DIR_S_TO_M),
    ("we",               1, DIR_M_TO_S),
    ("err",              1, DIR_S_TO_M),
]


class WishbonePipelined(Record):
    def __init__(self, data_width=32, adr_width=30):
        self.data_width = data_width
        self.adr_width  = adr_width
        Record.__init__(self, set_layout_parameters(wb_pipelined_layout,
            adr_width  = adr_width,
            data_width = data_width,
            sel_width  = data_width//8))
        self.adr.reset_less   = True
        self.dat_w.reset_less = True
        self.dat_r.reset_less = True
        self.sel.reset_less   = True


class WishboneClassic2Pipeline(Module):
    def __init__(self, wb_classic, wb_pipelined):
        self.wb_classic = wb_classic
        self.wb_pipelined = wb_pipelined

        assert wb_classic.adr_width == wb_pipelined.adr_width
        assert wb_classic.data_width == wb_pipelined.data_width

        self.specials += Instance("wbc2pipeline",
            # Parameters
            p_AW       = wb_classic.adr_width,
            p_DW       = wb_classic.data_width,
            # Clock and reset
            i_i_clk    = ClockSignal(),
            i_i_reset  = ResetSignal(),
            # WB classic master -> slave
            i_i_mcyc   = wb_classic.cyc,
            i_i_mstb   = wb_classic.stb,
            i_i_mwe    = wb_classic.we,
            i_i_maddr  = wb_classic.adr,
            i_i_mdata  = wb_classic.dat_w,
            i_i_msel   = wb_classic.sel,
            i_i_mcti   = wb_classic.cti,
            i_i_mbte   = wb_classic.bte,
            # WB classic master <- slave
            o_o_mack   = wb_classic.ack,
            o_o_mdata  = wb_classic.dat_r,
            o_o_merr   = wb_classic.err,
            # WB pipelined master -> slave
            o_o_scyc   = wb_pipelined.cyc,
            o_o_sstb   = wb_pipelined.stb,
            o_o_swe    = wb_pipelined.we,
            o_o_saddr  = wb_pipelined.adr,
            o_o_sdata  = wb_pipelined.dat_w,
            o_o_ssel   = wb_pipelined.sel,
            # WB pipelined master <- slave
            i_i_sstall = wb_pipelined.stall,
            i_i_sack   = wb_pipelined.ack,
            i_i_sdata  = wb_pipelined.dat_r,
            i_i_serr   = wb_pipelined.err,
        )

    def add_sources(self, platform):
        this_dir = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
        platform.add_source(this_dir, "wbc2pipeline.v")


class WishbonePipelined2AXI(Module):
    def __init__(self, wb, axi, axi_write_id=0, axi_read_id=1, lgfifo=6, base_address=0x00000000):
        self.wb = wb
        self.axi = axi

        # always check the simulation-only test from the module
        assert axi.data_width >= wb.data_width, \
            (axi.data_width, wb.data_width)
        assert axi.address_width == wb.adr_width + log2_int(wb.data_width, need_pow2=False) - 3, \
            (axi.address_width, wb.adr_width, wb.data_width)
        assert (axi.data_width // wb.data_width) in [1, 2, 4, 8, 16, 32], \
            (axi.data_width, wb.data_width)

        wb_adr = Signal.like(wb.adr)
        self.comb += wb_adr.eq(wb.adr - base_address)

        self.specials += Instance("wbm2axisp",
            # Parameters
            p_C_AXI_DATA_WIDTH = axi.data_width,
            p_C_AXI_ADDR_WIDTH = axi.address_width,
            p_C_AXI_ID_WIDTH   = axi.id_width,
            p_DW               = wb.data_width,
            p_AW               = wb.adr_width,
            p_AXI_WRITE_ID     = axi_write_id,
            p_AXI_READ_ID      = axi_read_id,
            p_LGFIFO           = lgfifo,
            # Clock and reset
            i_i_clk            = ClockSignal(),  # System clock
            i_i_reset          = ResetSignal(),  # Reset signal, drives AXI rst
            # AXI write address channel signals
            o_o_axi_awvalid    = axi.aw.valid,  # Write address valid
            i_i_axi_awready    = axi.aw.ready,  # Slave is ready to accept
            o_o_axi_awid       = axi.aw.id,     # Write ID
            o_o_axi_awaddr     = axi.aw.addr,   # Write address
            o_o_axi_awlen      = axi.aw.len,    # Write Burst Length
            o_o_axi_awsize     = axi.aw.size,   # Write Burst size
            o_o_axi_awburst    = axi.aw.burst,  # Write Burst type
            o_o_axi_awlock     = axi.aw.lock,   # Write lock type
            o_o_axi_awcache    = axi.aw.cache,  # Write Cache type
            o_o_axi_awprot     = axi.aw.prot,   # Write Protection type
            o_o_axi_awqos      = axi.aw.qos,    # Write Quality of Svc
            # AXI write data channel signals
            o_o_axi_wvalid     = axi.w.valid,   # Write valid
            i_i_axi_wready     = axi.w.ready,   # Write data ready
            o_o_axi_wdata      = axi.w.data,    # Write data
            o_o_axi_wstrb      = axi.w.strb,    # Write strobes
            o_o_axi_wlast      = axi.w.last,    # Last write transaction
            # AXI write response channel signals
            i_i_axi_bvalid     = axi.b.valid,   # Write reponse valid
            o_o_axi_bready     = axi.b.ready,   # Response ready
            i_i_axi_bid        = axi.b.id,      # Response ID
            i_i_axi_bresp      = axi.b.resp,    # Write response
            # AXI read address channel signals
            o_o_axi_arvalid    = axi.ar.valid,  # Read address valid
            i_i_axi_arready    = axi.ar.ready,  # Read address ready
            o_o_axi_arid       = axi.ar.id,     # Read ID
            o_o_axi_araddr     = axi.ar.addr,   # Read address
            o_o_axi_arlen      = axi.ar.len,    # Read Burst Length
            o_o_axi_arsize     = axi.ar.size,   # Read Burst size
            o_o_axi_arburst    = axi.ar.burst,  # Read Burst type
            o_o_axi_arlock     = axi.ar.lock,   # Read lock type
            o_o_axi_arcache    = axi.ar.cache,  # Read Cache type
            o_o_axi_arprot     = axi.ar.prot,   # Read Protection type
            o_o_axi_arqos      = axi.ar.qos,    # Read Protection type
            # AXI read data channel signals
            i_i_axi_rvalid     = axi.r.valid,   # Read reponse valid
            o_o_axi_rready     = axi.r.ready,   # Read Response ready
            i_i_axi_rid        = axi.r.id,      # Response ID
            i_i_axi_rdata      = axi.r.data,    # Read data
            i_i_axi_rresp      = axi.r.resp,    # Read response
            i_i_axi_rlast      = axi.r.last,    # Read last
            # We'll share the clock and the reset
            i_i_wb_cyc         = wb.cyc,
            i_i_wb_stb         = wb.stb,
            i_i_wb_we          = wb.we,
            i_i_wb_addr        = wb_adr,
            i_i_wb_data        = wb.dat_w,
            i_i_wb_sel         = wb.sel,
            o_o_wb_stall       = wb.stall,
            o_o_wb_ack         = wb.ack,
            o_o_wb_data        = wb.dat_r,
            o_o_wb_err         = wb.err,
        )

    def add_sources(self, platform):
        this_dir = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
        platform.add_sources(os.path.join(this_dir, "verilog"),
                             "wbm2axisp.v", "skidbuffer.v")
