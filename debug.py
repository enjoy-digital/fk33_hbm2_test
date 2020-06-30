from migen import *
from migen.genlib.misc import WaitTimer

from litex.soc.interconnect.csr import *
from litex.soc.interconnect import wishbone

ERROR_DAT_R = 0xdec0adba


class BusCSRDebug(Module, AutoCSR):
    def __init__(self, description, trigger, reset=None):
        if reset is None:
            self.reset = CSR()
            reset = self.reset.re
        self.count = CSRStatus(32)

        updates = []
        for name, signal in description.items():
            csr = CSRStatus(len(signal), name=name)
            setattr(self, name, csr)
            updates.append(csr.status.eq(signal))

        self.sync += [
            If(reset, self.count.status.eq(0)),
            If(trigger,
                self.count.status.eq(self.count.status + 1),
               *updates,
            ),
        ]


class WishboneSoftControl(Module, AutoCSR):
    def __init__(self, wb):
        self.wb = wb
        self.write = CSR()
        self.read = CSR()
        self.data = CSRStorage(wb.data_width)
        self.adr = CSRStorage(wb.adr_width)
        adr = Signal(wb.adr_width)
        data = Signal(wb.data_width)

        self.submodules.fsm = FSM()
        self.fsm.act("IDLE",
            If(self.write.re,
                NextValue(adr, self.adr.storage),
                NextValue(data, self.data.storage),
                NextState("WRITE")
            ),
            If(self.read.re,
                NextValue(adr, self.adr.storage),
                NextState("READ")
            ),
        )
        self.fsm.act("WRITE",
            wb.adr.eq(adr),
            wb.dat_w.eq(data),
            wb.sel.eq(2**len(wb.sel) - 1),
            wb.we.eq(1),
            wb.cyc.eq(1),
            wb.stb.eq(1),
            If(wb.ack,
               NextState("IDLE")
            ),
        )
        self.fsm.act("READ",
            wb.adr.eq(adr),
            wb.sel.eq(2**len(wb.sel) - 1),
            wb.we.eq(0),
            wb.cyc.eq(1),
            wb.stb.eq(1),
            If(wb.ack,
               If(wb.err,
                   NextValue(data, ERROR_DAT_R),
               ).Else(
                   NextValue(self.data.storage, wb.dat_r),
               ),
               NextState("IDLE")
            ),
        )


class WishboneSoftInjector(Module, AutoCSR):
    def __init__(self, wb_cpu, wb_csr):
        self.wb_slave = wishbone.Interface.like(wb_cpu)
        self.soft_control = CSRStorage(reset=1)
        self.comb += [
            If(self.soft_control.storage,
                wb_csr.connect(self.wb_slave),
                wb_cpu.dat_r.eq(ERROR_DAT_R),
                wb_cpu.ack.eq(wb_cpu.cyc & wb_cpu.stb),
            ).Else(
                wb_cpu.connect(self.wb_slave)
            )
        ]


class WishboneGuard(Module, AutoCSR):
    def __init__(self, master):
        # must be connected in SoC's do_finalize
        self.timeout = Signal()

        self.master = master
        self.slave = slave = wishbone.Interface.like(master)

        self.timeouts = CSRStatus(32)
        self.limit = CSRStorage(32, reset=100)
        self.reset = CSR()

        self.sync += [
            If(master.cyc & self.timeout,
                self.timeouts.status.eq(self.timeouts.status + 1)
            )
        ]

        self.comb += [
            If(self.timeouts.status < self.limit.storage,
                master.connect(slave)
            )
        ]
