"""
Host-side fleet manager for the rbamp package (v1.3).

Thin manager layered over single-device :class:`RbAmp` calls. Owns a list
of device handles (members + excluded) and provides bus scan, batched
polling, General-Call period synchronisation, fleet-wide aggregation,
and provisioning helpers.

Every fleet operation delegates to the per-device :class:`RbAmp` API —
the manager adds no new wire protocol, only orchestration. Failure
isolation is a core property: one unreachable module never aborts a
batch (poll/aggregate/sync skip the dead module and continue).

Mirror of ``libs/arduino/RbAmp/src/RbAmpFleet.h`` (commit ``c5726bc``)
adapted to Python idioms (collections instead of fixed arrays, exceptions
instead of error codes, ``with`` statement instead of explicit destruct).

Wedged-bus hazard (L8): For MicroPython on ESP32 the underlying IDF
``i2c_master`` driver can spin on a marginal bus (weak pull-ups, EMI under
load) BELOW this manager. :meth:`poll_all` / :meth:`total_power` etc. can
block and cannot be interrupted from the same task. Mitigation = proper
external pull-ups + an app-level task-WDT that reboots cleanly out of a
wedge. CPython hosts via USB-I2C adapters typically surface NACK as
``OSError`` and the backend retry-then-fail policy bounds the wait.

Example::

    from rbamp import RbAmpFleet

    fleet = RbAmpFleet(bus)
    fleet.scan()                              # adopts every healthy rbAmp
    fleet.enable_gc_all(group=0)              # opt-in GC latch on each
    fleet.gc_latch(group=0, tick=42)          # broadcast 5-byte GC frame
    sync = fleet.check_sync(expected_tick=42)
    for member, info in zip(fleet, sync):
        if not info.in_sync:
            print("dropped:", member.address, info)

    snaps = fleet.poll_all()
    print("total W:", fleet.total_power())
    print("total Wh:", fleet.total_energy_wh())
"""

from ._snapshot import (
    RbAmpError,
    RbAmpIOError,
    RbAmpParamError,
)
from . import _registers_v2 as R


# Maximum modules a single fleet can track (members + excluded each capped).
RBAMP_FLEET_MAX_MODULES = 16


class RbAmpFleetSync:
    """Per-module result of :meth:`RbAmpFleet.check_sync` — GC latch verify.

    Attributes:
        addr (int): Module 7-bit address.
        gc_tick (int): ``REG_GC_TICK`` read back; ``0xFFFF`` = never received.
        in_sync (bool): ``gc_tick == expected_tick``.
        reachable (bool): The GC_TICK read succeeded.
    """

    def __init__(self, addr, gc_tick=0xFFFF, in_sync=False, reachable=False):
        self.addr = addr
        self.gc_tick = gc_tick
        self.in_sync = in_sync
        self.reachable = reachable

    def __repr__(self):
        return "RbAmpFleetSync(addr=0x{:02X}, tick=0x{:04X}, in_sync={}, reachable={})".format(
            self.addr, self.gc_tick, self.in_sync, self.reachable
        )


class RbAmpFleetPoll:
    """Per-module result of :meth:`RbAmpFleet.poll_all` — one snapshot attempt.

    Attributes:
        addr (int): Module 7-bit address.
        ok (bool): The full RT snapshot read succeeded.
        channels (int): Channel count from the module's variant.
    """

    def __init__(self, addr, ok=False, channels=0):
        self.addr = addr
        self.ok = ok
        self.channels = channels

    def __repr__(self):
        return "RbAmpFleetPoll(addr=0x{:02X}, ok={}, channels={})".format(
            self.addr, self.ok, self.channels
        )


class RbAmpFleet:
    """Manager for N rbAmp modules sharing one I²C bus.

    Construct with a shared bus, then either :meth:`scan` the bus for
    modules or :meth:`add` externally-constructed :class:`RbAmp` handles.

    Args:
        bus: An open ``smbus2.SMBus`` (CPython) or ``machine.I2C``
            (MicroPython) bus. The fleet does NOT take ownership of the
            bus.
        max_modules (int): Soft cap on adopted members; subsequent
            adoptions raise :class:`RbAmpParamError`.
    """

    def __init__(self, bus, max_modules=RBAMP_FLEET_MAX_MODULES):
        self._bus = bus
        self._max = int(max_modules)
        self._members = []   # list[RbAmp]
        self._excluded = []  # list[int] — addresses dropped during scan
        self._last_tick = 0  # uint16 monotonic GC tick counter

    # ---- Membership / query --------------------------------------------------

    def __iter__(self):
        return iter(self._members)

    def __len__(self):
        return len(self._members)

    def __getitem__(self, idx):
        return self._members[idx]

    @property
    def count(self):
        """Number of tracked members."""
        return len(self._members)

    @property
    def excluded(self):
        """Tuple of addresses dropped during :meth:`scan` as conflicts."""
        return tuple(self._excluded)

    def find(self, addr):
        """Return the :class:`RbAmp` member with live address ``addr`` or None."""
        for m in self._members:
            if m.address == addr:
                return m
        return None

    def add(self, dev):
        """Adopt an externally-constructed :class:`RbAmp` handle.

        Args:
            dev: A :class:`RbAmp` instance whose ``begin()`` succeeded.

        Raises:
            RbAmpParamError: dev is None, address already tracked, or
                the fleet is at ``max_modules``.
        """
        if dev is None:
            raise RbAmpParamError("add(None) is not allowed")
        if any(m.address == dev.address for m in self._members):
            raise RbAmpParamError(
                "address 0x{:02X} already in fleet".format(dev.address)
            )
        if len(self._members) >= self._max:
            raise RbAmpParamError(
                "fleet full ({} modules; raise max_modules)".format(self._max)
            )
        self._members.append(dev)

    # ---- Scan ----------------------------------------------------------------

    def scan(self, match_product=True):
        """Probe addresses ``0x08..0x77``, adopt every healthy rbAmp.

        Per truth-doc §1: detection = REG_HW_VARIANT (0x55) ∈ 1..6. If
        ``match_product`` is True, additionally verifies
        ``REG_PRODUCT_ID`` == 0x01 (rbAmp sensor family) to skip
        rbDimmer / other family devices.

        Args:
            match_product (bool): Filter on PRODUCT_ID == 0x01.

        Returns:
            int: Number of modules adopted in this call.
        """
        from . import RbAmp as _RbAmp

        added = 0
        for addr in range(0x08, 0x78):
            if any(m.address == addr for m in self._members):
                continue
            try:
                candidate = _RbAmp(self._bus, addr=addr)
                candidate.begin()
            except RbAmpError:
                continue
            if match_product:
                try:
                    if candidate.read_product_id() != 0x01:
                        continue
                except RbAmpError:
                    continue
            try:
                self.add(candidate)
                added += 1
            except RbAmpParamError:
                self._excluded.append(addr)
            if len(self._members) >= self._max:
                break
        return added

    # ---- Poll ----------------------------------------------------------------

    def poll_all(self):
        """Read an RT snapshot from every member.

        Failure isolation: a NACKing module's poll record gets ``ok=False``
        and the loop continues — never aborts the batch.

        Returns:
            list[tuple[RbAmpSnapshot|None, RbAmpFleetPoll]]: One tuple per
            tracked member, in the same order as :meth:`__iter__`.
        """
        out = []
        for m in self._members:
            try:
                snap = m.read_all()
                out.append((snap, RbAmpFleetPoll(m.address, ok=True,
                                                  channels=m.channels)))
            except RbAmpError:
                out.append((None, RbAmpFleetPoll(m.address, ok=False,
                                                  channels=m.channels)))
        return out

    # ---- General-Call fleet sync --------------------------------------------

    def enable_gc_all(self, group=0):
        """Enable GC latch reception on every member (persisted + reset).

        Per member: optionally :meth:`RbAmp.set_group_id` (if non-zero),
        then :meth:`RbAmp.enable_gc`. Blocks ~1 s per module (save 700 ms
        + reset settle). A per-module failure is skipped (loop continues).

        Args:
            group (int): Group id to assign (0 = leave group unchanged).

        Returns:
            int: Number of members enabled successfully.
        """
        ok = 0
        for m in self._members:
            try:
                if group != 0:
                    m.set_group_id(group)
                m.enable_gc(True)
                ok += 1
            except RbAmpError:
                continue
        return ok

    def gc_latch(self, group=0, tick=None, settle_ms=50):
        """Broadcast a 5-byte GC LATCH frame.

        Args:
            group (int): Group filter (0 = all-call).
            tick (int|None): 16-bit tick stored in each module's
                ``REG_GC_TICK``. ``None`` = auto-increment the fleet's
                internal counter (wraps at 0xFFFF).
            settle_ms (int): Sleep after the broadcast before reads.

        Returns:
            int: The tick that was broadcast (useful for the subsequent
                :meth:`check_sync` call).
        """
        from . import RbAmp as _RbAmp
        if tick is None:
            self._last_tick = (self._last_tick + 1) & 0xFFFF
            tick = self._last_tick
        else:
            self._last_tick = tick & 0xFFFF
        _RbAmp.broadcast_latch_group(self._bus, group=group, tick=tick)
        if settle_ms:
            # Sleep through the first member's backend — any time abstraction works.
            if self._members:
                self._members[0]._io.sleep_ms(settle_ms)
        return self._last_tick

    def check_sync(self, expected_tick):
        """Verify per-module GC sync by reading ``REG_GC_TICK`` on each.

        Args:
            expected_tick (int): Tick value the last :meth:`gc_latch` broadcast.

        Returns:
            list[RbAmpFleetSync]: One entry per tracked member.
        """
        out = []
        expected = expected_tick & 0xFFFF
        for m in self._members:
            sync = RbAmpFleetSync(m.address)
            try:
                sync.gc_tick = m.read_gc_tick()
                sync.reachable = True
                sync.in_sync = (sync.gc_tick == expected)
            except RbAmpError:
                pass
            out.append(sync)
        return out

    # ---- Aggregation ---------------------------------------------------------

    def total_power(self):
        """Sum real power across every member × channel (W).

        Returns:
            float: Total real power; a NACKing channel contributes 0.
        """
        total = 0.0
        for m in self._members:
            for ch in range(m.channels):
                try:
                    total += m.read_power(ch)
                except RbAmpError:
                    pass
        return total

    def total_energy_wh(self):
        """Sum per-device Wh accumulators across the fleet.

        Returns:
            float: Total energy in Wh (signed; negative = net export).
        """
        return sum(
            m.energy.wh(ch)
            for m in self._members
            for ch in range(m.channels)
        )

    def poll_errors(self):
        """Read durable error flag (``EVENT_ERROR`` bit3) on every member.

        Returns:
            dict[int, bool]: Map ``addr → has_error``. NACKing modules
            entry value is True (treated as suspect).
        """
        out = {}
        for m in self._members:
            try:
                out[m.address] = m.has_error()
            except RbAmpError:
                out[m.address] = True
        return out
