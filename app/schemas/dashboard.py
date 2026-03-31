"""Dashboard summary API."""

from pydantic import BaseModel, ConfigDict, Field


class DashboardSummaryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arrivals_today: int = Field(ge=0)
    departures_today: int = Field(ge=0)
    occupied_rooms: int = Field(ge=0)
    total_rooms: int = Field(ge=0)
    dirty_rooms: int = Field(ge=0)
    currency: str
