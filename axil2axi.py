from migen import *

from litex.soc.interconnect.axi import AXIInterface, AXILiteInterface


class AXILite2AXI(Module):
    def __init__(self, axi_lite, axi, write_id=0, read_id=0, burst_type="FIXED"):
        assert isinstance(axi_lite, AXILiteInterface)
        assert isinstance(axi, AXIInterface)
        assert axi_lite.data_width == axi.data_width
        assert axi_lite.address_width == axi.address_width

        burst_size = log2_int(axi.data_width // 8)
        # burst type has no meaning as we use burst length of 1, but the AXI slave may requires
        # certain type of burst
        burst_type = {
            "FIXED": 0b00,
            "INCR": 0b01,
            "WRAP": 0b10,
        }[burst_type]

        self.comb += [
            axi.aw.valid.eq(axi_lite.aw.valid),
            axi_lite.aw.ready.eq(axi.aw.ready),
            axi.aw.addr.eq(axi_lite.aw.addr),
            axi.aw.burst.eq(burst_type),
            axi.aw.len.eq(0),
            axi.aw.size.eq(burst_size),
            axi.aw.lock.eq(0),
            axi.aw.prot.eq(0),
            axi.aw.cache.eq(0b0011),
            axi.aw.qos.eq(0),
            axi.aw.id.eq(write_id),

            axi.w.valid.eq(axi_lite.w.valid),
            axi_lite.w.ready.eq(axi.w.ready),
            axi.w.data.eq(axi_lite.w.data),
            axi.w.strb.eq(axi_lite.w.strb),
            axi.w.last.eq(1),

            axi_lite.b.valid.eq(axi.b.valid),
            axi_lite.b.resp.eq(axi.b.resp),
            axi.b.ready.eq(axi_lite.b.ready),

            axi.ar.valid.eq(axi_lite.ar.valid),
            axi_lite.ar.ready.eq(axi.ar.ready),
            axi.ar.addr.eq(axi_lite.ar.addr),
            axi.ar.burst.eq(burst_type),
            axi.ar.len.eq(0),
            axi.ar.size.eq(burst_size),
            axi.ar.lock.eq(0),
            axi.ar.prot.eq(0),
            axi.ar.cache.eq(0b0011),
            axi.ar.qos.eq(0),
            axi.ar.id.eq(read_id),

            axi_lite.r.valid.eq(axi.r.valid),
            axi_lite.r.resp.eq(axi.r.resp),
            axi_lite.r.data.eq(axi.r.data),
            axi.r.ready.eq(axi_lite.r.ready),
        ]
