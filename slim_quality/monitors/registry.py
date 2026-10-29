"""Every tool, by `name@version`. A released tool is never edited or removed: a change is a new version."""

from slim_quality.monitors.base import key
from slim_quality.monitors.cusum import Cusum
from slim_quality.monitors.ewma import Ewma
from slim_quality.monitors.exceedance import Exceedance
from slim_quality.monitors.projection import Projection
from slim_quality.monitors.shared import SharedShift
from slim_quality.monitors.westgard import Westgard

SERIES_TOOLS = {key(m): m for m in (Westgard(), Ewma(), Cusum(), Exceedance(), Projection())}
GROUP_TOOLS = {key(m): m for m in (SharedShift(),)}
TOOLS = SERIES_TOOLS | GROUP_TOOLS
