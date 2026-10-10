"""Typed models and parsers for the Clever mobile API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def month_fraction(now: datetime) -> float:
    """Return how much of ``now``'s calendar month has passed, from 0 to 1."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return (now - start) / (following - start)


def parse_datetime(value: Any) -> datetime | None:
    """Parse an API timestamp into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        # Clever consumption timestamps are Unix microseconds.
        return datetime.fromtimestamp(value / 1_000_000, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).astimezone(UTC)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class Profile:
    """Clever customer profile."""

    customer_id: str
    firebase_profile_id: str
    email: str
    first_name: str
    last_name: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Profile:
        return cls(
            customer_id=str(data.get("customerId") or data.get("id") or ""),
            firebase_profile_id=str(data.get("id") or ""),
            email=str(data.get("email") or ""),
            first_name=str(data.get("firstname") or ""),
            last_name=str(data.get("lastname") or ""),
        )


@dataclass(frozen=True)
class SmartChargingSettings:
    """Smart-charging settings embedded in an installation response."""

    enabled: bool = False
    supported: bool = False
    departure_time: str | None = None
    power_required: int | None = None
    phase_count: int | None = None
    preheat_minutes: int = 0

    @classmethod
    def from_installation(cls, data: dict[str, Any]) -> SmartChargingSettings:
        configuration = data.get("smartChargingConfiguration") or {}
        user = configuration.get("userConfiguration") or {}
        status = user.get("status") or {}
        departure = user.get("departureTime") or {}
        desired_range = user.get("desiredRange") or {}
        effect = user.get("configuredEffect") or {}
        return cls(
            enabled=bool(
                status.get("isEnabled", data.get("smartChargingIsEnabled", False))
            ),
            supported=bool(status.get("isSupported", False)),
            departure_time=departure.get("time"),
            power_required=_optional_int(desired_range.get("desiredRange")),
            phase_count=_optional_int(effect.get("phaseCount")),
            preheat_minutes=_optional_int(user.get("preheatInMinutes")) or 0,
        )


@dataclass(frozen=True)
class Installation:
    """A Clever home charger installation."""

    installation_id: str
    charge_box_id: str
    connector_id: int
    status: str
    is_online: bool
    model: str | None
    supports_locking: bool
    smart_charging: SmartChargingSettings

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Installation:
        charge_box_type = data.get("chargeBoxType") or {}
        capabilities = data.get("capabilities") or {}
        return cls(
            installation_id=str(data.get("installationId") or ""),
            charge_box_id=str(data.get("chargeBoxId") or ""),
            connector_id=int(data.get("connectorId") or 0),
            status=str(
                data.get("detailedInstallationStatus")
                or data.get("installationStatus")
                or "Unknown"
            ),
            is_online=bool(data.get("isOnline", False)),
            model=charge_box_type.get("name") or data.get("modelSeries"),
            supports_locking=bool(capabilities.get("supportsLocking", False)),
            smart_charging=SmartChargingSettings.from_installation(data),
        )


@dataclass(frozen=True)
class ChargingProfile:
    """Current v6 smart-charging profile."""

    profile_id: str
    profile_type: str
    charge_points: tuple[tuple[str, int], ...]
    enabled: bool
    departure_time: str | None
    power_required: int | None
    preheat_minutes: int
    happy_hour_enabled: bool

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ChargingProfile:
        points: list[tuple[str, int]] = []
        for location in (data.get("filters") or {}).get("locations") or []:
            for point in location.get("chargePoints") or []:
                points.append(
                    (str(point.get("id") or ""), int(point.get("connectorId") or 0))
                )
        settings = data.get("strategySettings") or {}
        return cls(
            profile_id=str(data.get("id") or ""),
            profile_type=str(data.get("type") or ""),
            charge_points=tuple(points),
            enabled=not bool(settings.get("disabled", False)),
            departure_time=settings.get("departureTime"),
            power_required=_optional_int(settings.get("powerRequired")),
            preheat_minutes=_optional_int(settings.get("preheatDurationMinutes")) or 0,
            happy_hour_enabled=bool(settings.get("happyHourEnabled", False)),
        )

    def matches(self, installation: Installation) -> bool:
        """Return whether this profile targets an installation connector."""
        return (
            installation.charge_box_id,
            installation.connector_id,
        ) in self.charge_points


@dataclass(frozen=True)
class ConsumptionRecord:
    """Completed charging-session consumption."""

    charge_point_id: str
    connector_id: int
    start: datetime | None
    stop: datetime | None
    kwh: float

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ConsumptionRecord:
        return cls(
            charge_point_id=str(data.get("chargePointId") or ""),
            connector_id=int(data.get("connectorId") or 0),
            start=parse_datetime(data.get("startTimeUtc")),
            stop=parse_datetime(data.get("stopTimeUtc")),
            kwh=float(data.get("kWh") or 0),
        )


@dataclass(frozen=True)
class ConsumptionSummary:
    """Values exposed by the monthly consumption sensors."""

    kwh_this_month: float = 0
    kwh_this_month_box: float | None = None
    last_charge: datetime | None = None


def summarize_consumption(
    records: list[ConsumptionRecord],
    month_start: datetime,
    charge_box_id: str | None = None,
) -> ConsumptionSummary:
    """Summarize consumption using the session start, matching existing behavior."""
    current = [
        record for record in records if record.start and record.start >= month_start
    ]
    total = sum(record.kwh for record in current)
    box_total = None
    if charge_box_id is not None:
        box_total = sum(
            record.kwh for record in current if record.charge_point_id == charge_box_id
        )
    stops = [record.stop for record in records if record.stop is not None]
    return ConsumptionSummary(
        kwh_this_month=round(total, 3),
        kwh_this_month_box=round(box_total, 3) if box_total is not None else None,
        last_charge=max(stops) if stops else None,
    )


@dataclass(frozen=True)
class EnergySurcharge:
    """Estimated energy surcharge for the current month."""

    start: datetime | None
    end: datetime | None
    price_dkk_per_kwh: float

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> EnergySurcharge:
        return cls(
            start=parse_datetime(data.get("startDate")),
            end=parse_datetime(data.get("endDate")),
            price_dkk_per_kwh=float(data.get("energySurchargePriceDkk") or 0),
        )


@dataclass(frozen=True)
class ChargePointState:
    """Live home-chargepoint state from Firestore."""

    charge_point_id: str
    connector_id: int
    status: str
    last_seen: datetime | None
    locked: bool | None

    @classmethod
    def from_firestore(cls, data: dict[str, Any]) -> ChargePointState:
        return cls(
            charge_point_id=str(data.get("chargePointId") or ""),
            connector_id=int(data.get("connectorId") or 0),
            status=str(data.get("status") or "Unknown"),
            last_seen=parse_datetime(data.get("lastSeen")),
            locked=data.get("locked") if isinstance(data.get("locked"), bool) else None,
        )


TERMINAL_TRANSACTION_STATES = {
    "cancelled",
    "completed",
    "expired",
    "failed",
    "stopped",
}


@dataclass(frozen=True)
class ActiveTransaction:
    """A transaction document retained in the Firestore active collection."""

    charge_point_id: str
    connector_id: int
    status: str
    consumed_wh: float
    timestamp: datetime | None
    charging_start: datetime | None
    charging_end: datetime | None
    smart_charging_flow: str | None
    vehicle_state_of_charge: dict[str, Any] = field(default_factory=dict)
    charging_plan: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_firestore(cls, data: dict[str, Any]) -> ActiveTransaction:
        return cls(
            charge_point_id=str(data.get("chargePointId") or ""),
            connector_id=int(data.get("connectorId") or 0),
            status=str(data.get("cpmsChargingStatus") or "Unknown"),
            consumed_wh=float(data.get("consumedWh") or 0),
            timestamp=parse_datetime(data.get("timeStamp")),
            charging_start=parse_datetime(data.get("chargingStart")),
            charging_end=parse_datetime(data.get("chargingEnd")),
            smart_charging_flow=(
                str(data["smartChargingFlow"])
                if data.get("smartChargingFlow") is not None
                else None
            ),
            vehicle_state_of_charge=data.get("vehicleStateOfCharge") or {},
            charging_plan=data.get("chargingPlan") or {},
        )

    @property
    def is_active(self) -> bool:
        return self.status.casefold() not in TERMINAL_TRANSACTION_STATES

    @property
    def target_energy_kwh(self) -> int | None:
        """Return the planned energy delivery for this session."""
        return _optional_int(self.charging_plan.get("powerRequiredInKwh"))

    @property
    def expected_completion(self) -> datetime | None:
        """Return the plan's earliest expected completion time."""
        return parse_datetime(self.charging_plan.get("earliestFinishedAt"))

    @property
    def planned_departure(self) -> datetime | None:
        return parse_datetime(self.charging_plan.get("departureTime"))

    @property
    def postponed_until(self) -> datetime | None:
        return parse_datetime(self.charging_plan.get("postponedUntil"))

    @property
    def battery_level(self) -> float | None:
        charge_state = self.vehicle_state_of_charge.get("vehicleChargeState") or {}
        return _optional_float(charge_state.get("batteryLevel"))

    @property
    def charge_limit(self) -> float | None:
        charge_state = self.vehicle_state_of_charge.get("vehicleChargeState") or {}
        return _optional_float(charge_state.get("chargeLimit"))

    @property
    def vehicle_is_plugged_in(self) -> bool | None:
        charge_state = self.vehicle_state_of_charge.get("vehicleChargeState") or {}
        value = charge_state.get("isPluggedIn")
        return value if isinstance(value, bool) else None

    def duration_seconds_at(self, now: datetime) -> float | None:
        """Return elapsed connection/charging time at ``now``."""
        if self.charging_start is None:
            return None
        end = self.charging_end or now
        return max(0, (end - self.charging_start).total_seconds())

    def average_power_kw_at(self, now: datetime) -> float | None:
        """Return average session power; the API has no instantaneous power field."""
        seconds = self.duration_seconds_at(now)
        if not seconds:
            return None
        return round((self.consumed_wh / 1000) / (seconds / 3600), 3)

    @property
    def progress_percent(self) -> float | None:
        target = self.target_energy_kwh
        if not target:
            return None
        return round(min(100, self.consumed_wh / (target * 10)), 1)

    def is_boosted_at(self, now: datetime) -> bool:
        """Return whether a boost segment is active at ``now``."""
        for segment in self.charging_plan.get("segments") or []:
            reason = segment.get("reason") or {}
            strategy = str(reason.get("strategy") or "").casefold()
            sub_strategy = str(reason.get("substrategy") or "").casefold()
            if strategy != "boost" and sub_strategy not in {"session", "timebox"}:
                continue
            start = parse_datetime(segment.get("start"))
            end = parse_datetime(segment.get("end"))
            if start and start <= now and (end is None or now < end):
                return True
        return False


@dataclass(frozen=True)
class CleverApiData:
    """Coordinator snapshot consumed by Home Assistant entities."""

    profile: Profile
    installations: tuple[Installation, ...]
    charging_profiles: tuple[ChargingProfile, ...]
    consumption: ConsumptionSummary
    surcharge: EnergySurcharge
    chargepoint_states: tuple[ChargePointState, ...]
    transactions: tuple[ActiveTransaction, ...]
    subscription_fee: float

    @property
    def energy_surcharge_cost(self) -> float:
        """Energy surcharge on this month's consumption so far, in DKK."""
        return round(
            self.consumption.kwh_this_month * self.surcharge.price_dkk_per_kwh, 2
        )

    @property
    def estimated_total_price(self) -> float:
        """Full subscription fee plus the surcharge so far, in DKK."""
        return round(self.subscription_fee + self.energy_surcharge_cost, 2)

    def effective_price_per_kwh_at(self, now: datetime) -> float | None:
        """Return what Clever has cost per charged kWh so far this month, in DKK.

        The subscription fee is counted in proportion to how much of the month
        has passed, so the figure is comparable with other electricity prices
        from the first charge of the month rather than only on the last day.
        The energy surcharge is added in full. Returns ``None`` until something
        has been charged.
        """
        kwh = self.consumption.kwh_this_month
        if kwh <= 0:
            return None
        fee_to_date = self.subscription_fee * month_fraction(now)
        return round((fee_to_date + self.energy_surcharge_cost) / kwh, 3)

    @property
    def home_installation(self) -> Installation | None:
        return self.installations[0] if self.installations else None

    @property
    def home_charging_profile(self) -> ChargingProfile | None:
        installation = self.home_installation
        if installation is None:
            return None
        return next(
            (
                profile
                for profile in self.charging_profiles
                if profile.matches(installation)
            ),
            next(
                (
                    profile
                    for profile in self.charging_profiles
                    if profile.profile_type.casefold() == "home"
                ),
                None,
            ),
        )

    @property
    def home_chargepoint_state(self) -> ChargePointState | None:
        installation = self.home_installation
        if installation is None:
            return None
        return next(
            (
                state
                for state in self.chargepoint_states
                if state.charge_point_id == installation.charge_box_id
                and state.connector_id == installation.connector_id
            ),
            None,
        )

    @property
    def active_home_transaction(self) -> ActiveTransaction | None:
        installation = self.home_installation
        if installation is None:
            return None
        candidates = [
            transaction
            for transaction in self.transactions
            if transaction.is_active
            and transaction.charge_point_id == installation.charge_box_id
            and transaction.connector_id == installation.connector_id
        ]
        return max(
            candidates,
            key=lambda transaction: (
                transaction.timestamp or datetime.min.replace(tzinfo=UTC)
            ),
            default=None,
        )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
