"""The equipment register and the reference lots (D-125…D-127): what the host calls. Flushes; the host
commits.

* `register_equipment`, `add_equipment_entry`: an item and its calibrations, checks, out of service.
  A failed calibration or check takes the item out of service by itself (the state follows the entries);
  `failure_nc` raises the nonconformity a visitor asks for in one click, its window from the last pass.
* `equipment_use`: what the items named on a prep batch or a run say on a day (D-126), by the profile.
* `register_lot`, `add_lot_entry`: a purchased lot with its certificate; opened, withdrawn.
  `lot_for_use`: the lot slim-prep's `StockLookup` asks for, through the host (P-28).
"""

from collections.abc import Iterable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import NotAllowed
from slim_quality.domain.equipment import Equipment, EquipmentEntry, EquipmentKind, UseProblem, check_use
from slim_quality.domain.materials import LotEntry, ReferenceLot
from slim_quality.domain.nonconformity import NcSource, Nonconformity
from slim_quality.persistence import store
from slim_quality.service import quality as q


class AlreadyRegistered(ValueError):
    pass


def register_equipment(
    session: Session,
    name: str,
    kind: EquipmentKind,
    actor: QualityActor,
    description: str = "",
    calibration_required: bool = True,
) -> Equipment:
    item = Equipment(
        name=name.strip(),
        kind=kind,
        description=description.strip(),
        calibration_required=calibration_required,
        registered_by=actor,
    )
    try:
        with session.begin_nested():
            return store.add_equipment(session, item)
    except IntegrityError:
        raise AlreadyRegistered(f"{item.name} is already in the register") from None


def add_equipment_entry(session: Session, item_id: int, entry: EquipmentEntry) -> Equipment:
    store.lock_equipment(session, item_id)
    store.get_equipment(session, item_id).check(entry)
    return store.add_equipment_entry(session, item_id, entry)


def register(session: Session, as_of: datetime | None = None) -> dict[str, Equipment]:
    return {item.name: item for item in store.list_equipment(session, as_of=as_of)}


def equipment_use(session: Session, names: Iterable[str], day: date) -> list[UseProblem]:
    """What the equipment used on `day` says under the profile in force: warn or block per item."""
    return check_use(register(session), names, day, q.current_profile(session).equipment)


def failure_nc(
    session: Session, item_id: int, index: int, actor: QualityActor, zone: ZoneInfo
) -> Nonconformity:
    """A nonconformity from the failed calibration or check at `index`: the item named where an NC names
    its instrument, the window from the last passing calibration to the failure (D-126)."""
    item = store.get_equipment(session, item_id)
    if not 0 <= index < len(item.entries) or not item.entries[index].failed:
        raise NotAllowed("raise it from a failed calibration or check")
    entry = item.entries[index]
    window = item.failure_window(index)
    span = None
    if window is not None:
        span = (datetime.combine(window[0], time.min, zone), datetime.combine(window[1], time.max, zone))
    what = entry.kind.label.lower()
    since = f" since its last pass on {window[0]}" if window else " (it never passed: no window)"
    return q.raise_nc(
        session,
        f"{item.name}: {what} failed on {entry.on}",
        f"{entry.note}\n\nWhich released results does it touch{since}?",
        actor,
        NcSource.EQUIPMENT,
        f"equipment:{item.id}:{index}",
        item.name,
        None,
        span,
    )


def register_lot(session: Session, lot: ReferenceLot) -> ReferenceLot:
    try:
        with session.begin_nested():
            return store.add_lot(session, lot)
    except IntegrityError:
        raise AlreadyRegistered(f"{lot} is already in the register") from None


def add_lot_entry(session: Session, lot_id: int, entry: LotEntry) -> ReferenceLot:
    store.lock_lot(session, lot_id)
    store.get_lot(session, lot_id).check(entry)
    return store.add_lot_entry(session, lot_id, entry)


def lot_for_use(session: Session, name: str, lot: str) -> ReferenceLot | None:
    """The registered lot, or None: the caller says why it can't be used (`unusable_on`)."""
    return store.find_lot(session, name, lot)
