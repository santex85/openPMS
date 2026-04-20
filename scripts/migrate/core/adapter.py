"""Abstract source adapter (one implementation per PMS)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from scripts.migrate.models.records import (
    BookingRecord,
    GuestRecord,
    RatePlanRecord,
    RoomRecord,
    RoomTypeRecord,
    ValidationResult,
)


class SourceAdapter(ABC):
    """Pull data from a PMS export and normalize to internal records."""

    @abstractmethod
    def extract_guests(self) -> list[GuestRecord]:
        raise NotImplementedError

    @abstractmethod
    def extract_room_types(self) -> list[RoomTypeRecord]:
        raise NotImplementedError

    @abstractmethod
    def extract_rooms(self) -> list[RoomRecord]:
        raise NotImplementedError

    @abstractmethod
    def extract_rate_plans(self) -> list[RatePlanRecord]:
        raise NotImplementedError

    @abstractmethod
    def extract_bookings(self) -> list[BookingRecord]:
        raise NotImplementedError

    @abstractmethod
    def validate(self) -> ValidationResult:
        """Pre-flight checks (files present, columns, etc.)."""

        raise NotImplementedError
