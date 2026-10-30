"""westgard@1: Westgard's multirules on a Levey-Jennings chart, each rule switched on or off.

z = (value − centre) ÷ SD, under the limits in force.
  1_2s  one point beyond ±2 SD (a warning, classically)
  1_3s  one point beyond ±3 SD (random error)
  2_2s  two in a row beyond the same ±2 SD (systematic)
  R_4s  one beyond +2 SD and the next beyond −2 SD, or the reverse (random)
  4_1s  four in a row beyond the same ±1 SD (systematic: drift)
  nx    n in a row on the same side of the centre (systematic: bias or drift); n = 10 by default
A point beyond 3 SD is flagged 1_3s, not 1_2s as well. Runs are flagged where they start to hold.
"""

from typing import ClassVar, Literal

from pydantic import Field

from slim_quality.domain.base import Frozen
from slim_quality.domain.enums import Severity
from slim_quality.monitors.base import Hit, Track, fmt, onset

Rule = Literal["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "nx"]


class WestgardParams(Frozen):
    rules: frozenset[Rule] = frozenset({"1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "nx"})
    n: int = Field(default=10, ge=6, le=12)  # the nx rule's run


class Westgard:
    name: ClassVar[str] = "westgard"
    version: ClassVar[int] = 1
    title: ClassVar[str] = "Westgard rules"
    codes: ClassVar[dict[str, Severity]] = {
        "WG_1_2S": Severity.INFO,
        "WG_1_3S": Severity.ACTION,
        "WG_2_2S": Severity.ACTION,
        "WG_R_4S": Severity.ACTION,
        "WG_4_1S": Severity.WARN,
        "WG_NX": Severity.WARN,
    }
    Params: ClassVar[type[Frozen]] = WestgardParams

    def check(self, track: Track, params: WestgardParams) -> list[Hit]:
        if track.limits is None:
            return []
        z = track.z()
        rules = params.rules
        hits = []
        for i, zi in enumerate(z):
            if "1_3s" in rules and abs(zi) > 3:
                hits.append(Hit("WG_1_3S", i, f"{fmt(zi)} SD from the centre"))
            elif "1_2s" in rules and abs(zi) > 2:
                hits.append(Hit("WG_1_2S", i, f"{fmt(zi)} SD from the centre"))
            if "R_4s" in rules and i and ((zi > 2 and z[i - 1] < -2) or (zi < -2 and z[i - 1] > 2)):
                hits.append(Hit("WG_R_4S", i, f"range {fmt(abs(zi - z[i - 1]))} SD between two points"))
        for sign, side in ((1, "above"), (-1, "below")):
            if "2_2s" in rules:
                for i in onset(_runs(z, 2, lambda v, s=sign: v * s > 2)):
                    hits.append(Hit("WG_2_2S", i, f"2 in a row beyond 2 SD {side}"))
            if "4_1s" in rules:
                for i in onset(_runs(z, 4, lambda v, s=sign: v * s > 1)):
                    hits.append(Hit("WG_4_1S", i, f"4 in a row beyond 1 SD {side}"))
            if "nx" in rules:
                for i in onset(_runs(z, params.n, lambda v, s=sign: v * s > 0)):
                    hits.append(Hit("WG_NX", i, f"{params.n} in a row {side} the centre"))
        return sorted(hits, key=lambda h: (h.index, h.code))


def _runs(z, n: int, test) -> list[bool]:
    """Whether the n points ending at each point all pass `test`."""
    held, count = [], 0
    for value in z:
        count = count + 1 if test(value) else 0
        held.append(count >= n)
    return held
