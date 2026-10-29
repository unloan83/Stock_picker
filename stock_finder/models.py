from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Driver:
    """One reason behind a verdict. Positive drivers support it, negative ones count against it."""

    text: str
    points: float  # contribution; negative for drags

    @property
    def positive(self) -> bool:
        return self.points > 0


@dataclass
class Score:
    score: float  # 0-100
    drivers: list[Driver] = field(default_factory=list)

    def top(self, positive: bool, n: int) -> list[Driver]:
        picked = [d for d in self.drivers if d.positive == positive and d.points != 0]
        return sorted(picked, key=lambda d: abs(d.points), reverse=True)[:n]


@dataclass
class IntradayPlan:
    direction: str  # "LONG" or "SHORT"
    entry: float
    stop: float
    target: float


@dataclass
class StockRequest:
    symbol: str


@dataclass
class OptionRequest:
    underlying: str
    strike: float
    kind: str  # "CE" or "PE"
    expiry_day: int | None = None
    expiry_month: int | None = None


@dataclass
class StockReport:
    symbol: str
    price: float | None
    verdict: str
    long_term: Score | None = None
    intraday: Score | None = None
    intraday_plan: IntradayPlan | None = None
    flags: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class OptionReport:
    label: str
    verdict: str
    score: Score | None = None
    expiry: date | None = None
    ltp: float | None = None
    spot: float | None = None
    iv: float | None = None
    pcr: float | None = None
    max_pain: float | None = None
    flags: list[str] = field(default_factory=list)
    error: str | None = None
