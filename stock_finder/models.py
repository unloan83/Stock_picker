from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Driver:
    """One scored parameter: its measured value, the level it is judged against, and the points it earned."""

    name: str
    value: str  # measured value, e.g. "18%"
    want: str  # the level that earns full points, e.g. ">15%"
    points: float  # earned; negative for a penalty
    max_points: float  # 0 when the parameter could not be scored (data missing)

    @property
    def scored(self) -> bool:
        return self.max_points > 0

    @property
    def positive(self) -> bool:
        return self.points > 0

    @property
    def text(self) -> str:
        return f"{self.name} {self.value}"


@dataclass
class Score:
    score: float  # 0-100
    drivers: list[Driver] = field(default_factory=list)

    def top(self, positive: bool, n: int) -> list[Driver]:
        """Biggest contributors (positive) or biggest shortfalls (not positive)."""
        picked = [d for d in self.drivers if d.scored and d.positive == positive]
        if positive:
            return sorted(picked, key=lambda d: d.points, reverse=True)[:n]
        return sorted(picked, key=lambda d: d.points - d.max_points)[:n]


@dataclass
class IntradayPlan:
    direction: str  # "LONG" or "SHORT"
    entry: float
    stop: float
    target: float


@dataclass
class StockRequest:
    query: str  # what the user typed: a symbol or a company name


@dataclass
class OptionRequest:
    underlying: str  # symbol or company name
    strike: float
    kind: str  # "CE" or "PE"
    expiry_day: int | None = None
    expiry_month: int | None = None


@dataclass
class StockReport:
    symbol: str
    price: float | None
    verdict: str
    name: str | None = None
    long_term: Score | None = None
    intraday: Score | None = None
    intraday_plan: IntradayPlan | None = None
    flags: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class OptionReport:
    label: str
    verdict: str
    underlying: str | None = None
    strike: float | None = None
    kind: str | None = None  # "CE" or "PE"
    score: Score | None = None
    expiry: date | None = None
    ltp: float | None = None
    spot: float | None = None
    iv: float | None = None
    pcr: float | None = None
    max_pain: float | None = None
    flags: list[str] = field(default_factory=list)
    error: str | None = None
