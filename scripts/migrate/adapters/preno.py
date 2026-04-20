"""Preno CSV adapter (guests + bookings exports)."""

from __future__ import annotations

import glob
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd

from scripts.migrate.core.adapter import SourceAdapter
from scripts.migrate.models.records import (
    BookingGuestSnapshot,
    BookingRecord,
    GuestRecord,
    RatePlanRecord,
    RoomRecord,
    RoomTypeRecord,
    ValidationIssue,
    ValidationResult,
)

# Minimal country / territory name → ISO 3166-1 alpha-2 (extend as needed).
_COUNTRY_TO_ISO2: dict[str, str] = {
    "thailand": "TH",
    "russian federation": "RU",
    "russia": "RU",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "united states": "US",
    "usa": "US",
    "united states of america": "US",
    "germany": "DE",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
    "china": "CN",
    "japan": "JP",
    "india": "IN",
    "australia": "AU",
    "new zealand": "NZ",
    "singapore": "SG",
    "malaysia": "MY",
    "indonesia": "ID",
    "vietnam": "VN",
    "philippines": "PH",
    "netherlands": "NL",
    "belgium": "BE",
    "switzerland": "CH",
    "austria": "AT",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "poland": "PL",
    "ukraine": "UA",
    "israel": "IL",
    "united arab emirates": "AE",
    "uae": "AE",
    "saudi arabia": "SA",
    "south africa": "ZA",
    "brazil": "BR",
    "mexico": "MX",
    "canada": "CA",
    "ireland": "IE",
    "portugal": "PT",
    "greece": "GR",
    "czech republic": "CZ",
    "czechia": "CZ",
    "hungary": "HU",
    "romania": "RO",
    "bulgaria": "BG",
    "croatia": "HR",
    "slovenia": "SI",
    "slovakia": "SK",
    "estonia": "EE",
    "latvia": "LV",
    "lithuania": "LT",
    "luxembourg": "LU",
    "iceland": "IS",
    "malta": "MT",
    "cyprus": "CY",
    "turkey": "TR",
    "türkiye": "TR",
    "egypt": "EG",
    "morocco": "MA",
    "tunisia": "TN",
    "kenya": "KE",
    "nigeria": "NG",
    "argentina": "AR",
    "chile": "CL",
    "colombia": "CO",
    "peru": "PE",
    "kazakhstan": "KZ",
    "south korea": "KR",
    "korea, republic of": "KR",
    "hong kong": "HK",
    "taiwan": "TW",
}


BookingStatusLiteral = Literal[
    "pending",
    "confirmed",
    "checked_in",
    "checked_out",
    "cancelled",
    "no_show",
]

_PRENO_STATUS_TO_INTERNAL: dict[str, str] = {
    "confirmed": "confirmed",
    "checked in": "checked_in",
    "checked out": "checked_out",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "no show": "no_show",
    "no-show": "no_show",
}


def _norm_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _column_lookup(df: pd.DataFrame) -> dict[str, str]:
    return {_norm_key(str(c)): str(c) for c in df.columns}


def _get_col(df: pd.DataFrame, lookup: dict[str, str], *candidates: str) -> str | None:
    for cand in candidates:
        k = _norm_key(cand)
        if k in lookup:
            return lookup[k]
    return None


def _to_bool_blacklist(v: Any) -> bool:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "y", "blacklisted"}


def _map_country_to_nationality(raw: Any) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) == 2 and s.isalpha():
        return s.upper()
    iso = _COUNTRY_TO_ISO2.get(_norm_key(s))
    return iso


def _parse_decimal(v: Any) -> Decimal | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


class PrenoAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        guests_glob: str | None,
        bookings_glob: str | None,
        rooms_csv: str | None = None,
        include_cancelled: bool = False,
    ) -> None:
        self._guests_glob = guests_glob
        self._bookings_glob = bookings_glob
        self._rooms_csv = rooms_csv
        self._include_cancelled = include_cancelled
        self._guests_df: pd.DataFrame | None = None
        self._bookings_df: pd.DataFrame | None = None
        self._rooms_df: pd.DataFrame | None = None

    def _load_frames(self, pattern: str | None, id_column: str) -> pd.DataFrame | None:
        if not pattern or not pattern.strip():
            return None
        paths = sorted(glob.glob(pattern))
        if not paths:
            return None
        frames: list[pd.DataFrame] = []
        for p in paths:
            frames.append(pd.read_csv(p, dtype=str, keep_default_na=False))
        if not frames:
            return None
        merged = pd.concat(frames, ignore_index=True)
        merged.columns = [str(c).strip() for c in merged.columns]
        id_col = _get_col(merged, _column_lookup(merged), id_column, "id", "ID")
        if id_col and id_col in merged.columns:
            merged[id_col] = merged[id_col].astype(str).str.strip()
            merged = merged.drop_duplicates(subset=[id_col], keep="first")
        return merged

    def _ensure_loaded(self) -> None:
        if self._guests_df is None and self._guests_glob:
            self._guests_df = self._load_frames(self._guests_glob, "id")
        if self._bookings_df is None and self._bookings_glob:
            self._bookings_df = self._load_frames(self._bookings_glob, "Booking ID")
        if self._rooms_df is None and self._rooms_csv:
            path = Path(self._rooms_csv)
            if path.is_file():
                self._rooms_df = pd.read_csv(path, dtype=str, keep_default_na=False)
                self._rooms_df.columns = [str(c).strip() for c in self._rooms_df.columns]

    def validate(self) -> ValidationResult:
        issues: list[ValidationIssue] = []
        if not self._guests_glob and not self._bookings_glob:
            issues.append(
                ValidationIssue(
                    message="At least one of --guests or --bookings glob is required",
                ),
            )
        if self._guests_glob:
            if not glob.glob(self._guests_glob):
                issues.append(
                    ValidationIssue(
                        message=f"No files match guests glob: {self._guests_glob!r}",
                    ),
                )
        if self._bookings_glob:
            if not glob.glob(self._bookings_glob):
                issues.append(
                    ValidationIssue(
                        message=f"No files match bookings glob: {self._bookings_glob!r}",
                    ),
                )
        if self._rooms_csv and not Path(self._rooms_csv).is_file():
            issues.append(
                ValidationIssue(
                    level="warning",
                    message=f"Rooms CSV not found: {self._rooms_csv!r} (will infer rooms from bookings)",
                ),
            )
        return ValidationResult(ok=not any(i.level == "error" for i in issues), issues=issues)

    def extract_guests(self) -> list[GuestRecord]:
        self._ensure_loaded()
        if self._guests_df is None or self._guests_df.empty:
            return []
        df = self._guests_df
        lu = _column_lookup(df)
        col_id = _get_col(df, lu, "id", "guest id", "guest_id")
        col_first = _get_col(df, lu, "name", "first name", "firstname")
        col_last = _get_col(df, lu, "surname", "last name", "lastname")
        col_email = _get_col(df, lu, "email")
        col_phone = _get_col(df, lu, "phone", "mobile")
        col_country = _get_col(df, lu, "country")
        col_note = _get_col(df, lu, "note", "notes")
        col_bl = _get_col(df, lu, "is_blacklisted", "blacklisted")

        if not col_id or not col_first or not col_last:
            return []

        out: list[GuestRecord] = []
        for _, row in df.iterrows():
            ext_id = str(row.get(col_id, "")).strip()
            if not ext_id:
                continue
            first = str(row.get(col_first, "")).strip() or "Unknown"
            last = str(row.get(col_last, "")).strip() or "Guest"
            email_raw = row.get(col_email, "") if col_email else ""
            phone_raw = row.get(col_phone, "") if col_phone else ""
            notes = str(row.get(col_note, "")).strip() if col_note else None
            if notes == "":
                notes = None
            country_raw = row.get(col_country, "") if col_country else None
            bl = _to_bool_blacklist(row.get(col_bl, False)) if col_bl else False
            out.append(
                GuestRecord(
                    external_id=ext_id,
                    first_name=first,
                    last_name=last,
                    email=str(email_raw).strip() if email_raw else None,
                    phone=str(phone_raw).strip() if phone_raw else None,
                    nationality=_map_country_to_nationality(country_raw),
                    notes=notes,
                    vip_status=bl,
                ),
            )
        return out

    def extract_bookings(self) -> list[BookingRecord]:
        self._ensure_loaded()
        if self._bookings_df is None or self._bookings_df.empty:
            return []
        df = self._bookings_df
        lu = _column_lookup(df)
        c_id = _get_col(df, lu, "Booking ID", "booking id", "booking_id")
        c_in = _get_col(df, lu, "Checkin date", "checkin date", "check_in", "arrival")
        c_out = _get_col(df, lu, "Checkout date", "checkout date", "check_out", "departure")
        c_rt = _get_col(df, lu, "Room types", "room types", "room_type")
        c_rp = _get_col(df, lu, "Rate plans", "rate plans", "rate_plan")
        c_gf = _get_col(
            df,
            lu,
            "Primary guest first name",
            "guest first name",
            "first name",
        )
        c_gl = _get_col(
            df,
            lu,
            "Primary guest last name",
            "guest last name",
            "last name",
        )
        c_country = _get_col(df, lu, "Country")
        c_source = _get_col(df, lu, "Source", "Channel")
        c_status = _get_col(df, lu, "Status")
        c_notes = _get_col(df, lu, "Notes", "Note")
        c_adults = _get_col(df, lu, "Adults")
        c_total = _get_col(df, lu, "Total")

        if not c_id or not c_in or not c_out or not c_rt or not c_rp:
            return []

        out: list[BookingRecord] = []
        for _, row in df.iterrows():
            ext = str(row.get(c_id, "")).strip()
            if not ext:
                continue
            raw_status = str(row.get(c_status, "")).strip().lower() if c_status else ""
            internal = _PRENO_STATUS_TO_INTERNAL.get(raw_status, "confirmed")
            if internal == "cancelled" and not self._include_cancelled:
                continue

            check_in = pd.to_datetime(row.get(c_in), errors="coerce")
            check_out = pd.to_datetime(row.get(c_out), errors="coerce")
            if pd.isna(check_in) or pd.isna(check_out):
                continue
            d_in = check_in.date()
            d_out = check_out.date()
            if d_out <= d_in:
                continue

            rt_name = str(row.get(c_rt, "")).split(",")[0].strip()
            rp_name = str(row.get(c_rp, "")).split(",")[0].strip()
            if not rt_name or not rp_name:
                continue

            gf = str(row.get(c_gf, "")).strip() if c_gf else ""
            gl = str(row.get(c_gl, "")).strip() if c_gl else ""
            if not gf:
                gf = "Guest"
            if not gl:
                gl = "Unknown"

            country = _map_country_to_nationality(row.get(c_country, "")) if c_country else None
            email_guess = f"noemail-{re.sub(r'[^a-zA-Z0-9_-]+', '-', ext)}@migrate.openpms.local"
            phone_guess = "+10000000000"
            guest = BookingGuestSnapshot(
                first_name=gf,
                last_name=gl,
                email=email_guess,
                phone=phone_guess,
                passport_data=None,
            )
            _ = country  # nationality not on GuestPayload; could extend later

            src = str(row.get(c_source, "direct")).strip() if c_source else "direct"
            notes = str(row.get(c_notes, "")).strip() if c_notes else None
            if notes == "":
                notes = None
            adults: int | None = None
            if c_adults:
                try:
                    adults = int(float(str(row.get(c_adults, "")).strip()))
                except Exception:
                    adults = None

            out.append(
                BookingRecord(
                    external_id=ext,
                    check_in=d_in,
                    check_out=d_out,
                    room_type_name=rt_name,
                    rate_plan_name=rp_name,
                    guest=guest,
                    status=cast(BookingStatusLiteral, internal),
                    source=src[:64],
                    notes=notes,
                    adults=adults,
                    total_source=_parse_decimal(row.get(c_total)) if c_total else None,
                ),
            )
        return out

    def extract_room_types(self) -> list[RoomTypeRecord]:
        bookings = self.extract_bookings()
        names = sorted({b.room_type_name for b in bookings})
        return [RoomTypeRecord(name=n) for n in names]

    def extract_rate_plans(self) -> list[RatePlanRecord]:
        bookings = self.extract_bookings()
        names = sorted({b.rate_plan_name for b in bookings})
        return [RatePlanRecord(name=n) for n in names]

    def extract_rooms(self) -> list[RoomRecord]:
        self._ensure_loaded()
        if self._rooms_df is not None and not self._rooms_df.empty:
            df = self._rooms_df
            lu = _column_lookup(df)
            c_name = _get_col(df, lu, "name", "room name", "Room")
            c_type = _get_col(df, lu, "room type", "type", "category")
            if c_name and c_type:
                recs: list[RoomRecord] = []
                for _, row in df.iterrows():
                    nm = str(row.get(c_name, "")).strip()
                    rt = str(row.get(c_type, "")).strip()
                    if nm and rt:
                        recs.append(RoomRecord(room_type_name=rt, name=nm))
                return recs
        return []
