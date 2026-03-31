"""
Scoring system for FVG trade setups.

Points:
    FVG detected          +40
    Trend aligned         +20
    Session active        +20
    Liquidity sweep       +20  (mocked as True for now)

Minimum score to trigger an alert: configured in config.json (default 80).
"""

from dataclasses import dataclass


@dataclass
class ScoreBreakdown:
    fvg: int
    trend: int
    session: int
    liquidity: int

    @property
    def total(self) -> int:
        return self.fvg + self.trend + self.session + self.liquidity

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "breakdown": {
                "fvg": self.fvg,
                "trend": self.trend,
                "session": self.session,
                "liquidity": self.liquidity,
            },
        }


def calculate_score(
    fvg_detected: bool,
    trend_aligned: bool,
    session_active: bool,
    liquidity_sweep: bool = True,  # mocked
) -> ScoreBreakdown:
    return ScoreBreakdown(
        fvg=40 if fvg_detected else 0,
        trend=20 if trend_aligned else 0,
        session=20 if session_active else 0,
        liquidity=20 if liquidity_sweep else 0,
    )
