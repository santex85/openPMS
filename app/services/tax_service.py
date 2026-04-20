"""Country-agnostic tax calculation from CountryPack JSONB tax rules."""

from dataclasses import dataclass
from collections import defaultdict, deque
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing.tax_config import TaxConfig, TaxMode
from app.models.core.country_pack import CountryPack
from app.schemas.country_pack import TaxCalculationResponse, TaxLineResponse
from app.schemas.tax_config import TaxBreakdown, TaxConfigCreate


@dataclass(frozen=True)
class CountryPackTaxPosting:
    """How country-pack taxes should be posted into folio for a property tax mode."""

    lines: list[TaxLineResponse]
    room_charge_amount: Decimal
    total_amount: Decimal


def _q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _rule_applies(applies_to: list[str], category: str) -> bool:
    norm = {str(x).strip().lower() for x in applies_to}
    if "all" in norm:
        return True
    return category.strip().lower() in norm


def _parse_rules(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, object]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    return out


def calculate_taxes_from_rules(
    base_price: Decimal,
    raw_rules: object,
    *,
    applies_to_category: str,
) -> TaxCalculationResponse:
    """
    Pure tax engine: no DB, no country logic.

    * Non-compound rules: tax amount = base_price * rate (exclusive) or gross extraction (inclusive).
    * Compound rules: tax amount = running_total * rate after all non-compound amounts applied,
      then each compound updates running_total (chain order by compound_after).
    """
    rules = _parse_rules(raw_rules)
    active: list[dict[str, object]] = []
    for r in rules:
        if not r.get("active", True):
            continue
        codes_applies = r.get("applies_to")
        if not isinstance(codes_applies, list):
            continue
        applies_list = [str(x) for x in codes_applies]
        if not _rule_applies(applies_list, applies_to_category):
            continue
        code = str(r.get("code", "")).strip()
        if not code:
            continue
        rate_raw = r.get("rate", 0)
        try:
            rate = Decimal(str(rate_raw))
        except Exception:
            rate = Decimal("0")
        if rate < 0:
            rate = Decimal("0")
        active.append(r)

    non_compound: list[dict[str, object]] = []
    compound: list[dict[str, object]] = []
    for r in active:
        ca = r.get("compound_after")
        if ca is None or (isinstance(ca, str) and not ca.strip()):
            non_compound.append(r)
        else:
            compound.append(r)

    lines: list[TaxLineResponse] = []
    running = base_price

    for r in non_compound:
        name = str(r.get("name", r.get("code", "")))
        code = str(r.get("code", ""))
        rate = Decimal(str(r.get("rate", 0)))
        inclusive = bool(r.get("inclusive", False))
        if inclusive:
            if rate == Decimal("0"):
                amt = Decimal("0.00")
            else:
                gross = base_price
                net = _q2(gross / (Decimal("1") + rate))
                amt = _q2(gross - net)
            lines.append(TaxLineResponse(code=code, name=name, amount=amt))
        else:
            amt = _q2(base_price * rate)
            lines.append(TaxLineResponse(code=code, name=name, amount=amt))
            running += amt

    if compound:
        by_code: dict[str, dict[str, object]] = {
            str(r.get("code", "")).strip(): r
            for r in active
            if str(r.get("code", "")).strip()
        }
        deps: dict[str, str | None] = {}
        for r in compound:
            c = str(r.get("code", "")).strip()
            ca = r.get("compound_after")
            dep = str(ca).strip() if isinstance(ca, str) else None
            deps[c] = dep

        indeg: defaultdict[str, int] = defaultdict(int)
        nodes = [str(r.get("code", "")).strip() for r in compound]
        for n in nodes:
            indeg[n] = 0
        adj: defaultdict[str, list[str]] = defaultdict(list)
        for c, dep in deps.items():
            if dep is not None and dep in by_code:
                adj[dep].append(c)
                indeg[c] += 1

        q = deque(sorted([n for n in nodes if indeg[n] == 0]))
        ordered: list[str] = []
        while q:
            n = q.popleft()
            ordered.append(n)
            for m in sorted(adj[n]):
                indeg[m] -= 1
                if indeg[m] == 0:
                    q.append(m)
        if len(ordered) != len(nodes):
            for n in nodes:
                if n not in ordered:
                    ordered.append(n)

        code_to_rule = {str(r.get("code", "")).strip(): r for r in compound}
        for code_key in ordered:
            r = code_to_rule.get(code_key)
            if r is None:
                continue
            name = str(r.get("name", code_key))
            rate = Decimal(str(r.get("rate", 0)))
            inclusive = bool(r.get("inclusive", False))
            if inclusive:
                net_before = _q2(running / (Decimal("1") + rate)) if rate else running
                amt = _q2(running - net_before)
                lines.append(
                    TaxLineResponse(code=code_key, name=name, amount=amt),
                )
            else:
                amt = _q2(running * rate)
                lines.append(
                    TaxLineResponse(code=code_key, name=name, amount=amt),
                )
                running += amt

    return TaxCalculationResponse(
        lines=lines,
        subtotal=base_price,
        total_with_taxes=_q2(running),
    )


def calculate_country_pack_tax_posting(
    base_price: Decimal,
    raw_rules: object,
    *,
    applies_to_category: str,
    mode: TaxMode | None,
) -> CountryPackTaxPosting:
    """
    Convert country-pack tax rules into folio posting behavior for the property's tax mode.

    * off: no tax lines, room charge remains gross.
    * exclusive / None: current behavior, room charge remains gross and taxes add on top.
    * inclusive: extract tax portions from the gross room charge and post them as separate
      lines while reducing the room charge line so the folio total stays unchanged.
    """
    gross = _q2(base_price)
    if mode == TaxMode.off:
        return CountryPackTaxPosting(lines=[], room_charge_amount=gross, total_amount=gross)

    adjusted_rules = _parse_rules(raw_rules)
    if mode == TaxMode.inclusive:
        adjusted_rules = [{**rule, "inclusive": True} for rule in adjusted_rules]

    calc = calculate_taxes_from_rules(
        gross,
        adjusted_rules,
        applies_to_category=applies_to_category,
    )
    if mode == TaxMode.inclusive:
        tax_total = _q2(sum((line.amount for line in calc.lines), Decimal("0.00")))
        room_charge_amount = _q2(gross - tax_total)
        return CountryPackTaxPosting(
            lines=calc.lines,
            room_charge_amount=room_charge_amount,
            total_amount=gross,
        )

    return CountryPackTaxPosting(
        lines=calc.lines,
        room_charge_amount=gross,
        total_amount=_q2(calc.total_with_taxes),
    )


async def calculate_taxes(
    session: AsyncSession,
    tenant_id: UUID,
    country_pack_code: str,
    base_price: Decimal,
    applies_to: str,
) -> TaxCalculationResponse:
    """
    Load pack taxes from DB (RLS: builtin + tenant custom) and calculate.
    """
    _ = tenant_id
    pack = await session.scalar(
        select(CountryPack).where(CountryPack.code == country_pack_code.strip()),
    )
    if pack is None:
        return TaxCalculationResponse(
            lines=[],
            subtotal=base_price,
            total_with_taxes=_q2(base_price),
        )
    return calculate_taxes_from_rules(
        base_price,
        pack.taxes,
        applies_to_category=applies_to,
    )


# --- Per-property tax (Phase 1): tax_configs + single-rate breakdown ---


def calculate_property_tax(amount: Decimal, config: TaxConfig) -> TaxBreakdown:
    """
    Property-level VAT/sales tax for receipts.

    * ``tax_mode == off`` or zero rate: no tax; gross == net == amount.
    * ``inclusive``: ``amount`` is the gross total; tax is extracted.
    * ``exclusive``: ``amount`` is the net total before tax; tax is added to gross.
    """
    rate = Decimal(str(config.tax_rate))
    if config.tax_mode == TaxMode.off or rate == 0:
        base = _q2(amount)
        return TaxBreakdown(
            tax_amount=Decimal("0.00"),
            gross_total=base,
            net_total=base,
        )
    if config.tax_mode == TaxMode.inclusive:
        gross = _q2(amount)
        tax = _q2(gross * rate / (Decimal("1") + rate))
        net = _q2(gross - tax)
        return TaxBreakdown(tax_amount=tax, gross_total=gross, net_total=net)
    # exclusive
    net = _q2(amount)
    tax = _q2(net * rate)
    gross = _q2(net + tax)
    return TaxBreakdown(tax_amount=tax, gross_total=gross, net_total=net)


def property_tax_summary_lines(config: TaxConfig, breakdown: TaxBreakdown) -> list[str]:
    """Human-readable lines for receipts (Notion acceptance wording)."""
    rate_pct = (Decimal(str(config.tax_rate)) * Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    name = config.tax_name.strip() or "Tax"
    if config.tax_mode == TaxMode.inclusive:
        return [
            f"Includes {name} {rate_pct}% = {format(breakdown.tax_amount, 'f')}",
        ]
    if config.tax_mode == TaxMode.exclusive:
        return [
            f"{name} {rate_pct}% = {format(breakdown.tax_amount, 'f')}",
        ]
    return []


async def get_tax_config(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> TaxConfig | None:
    return await session.scalar(
        select(TaxConfig).where(
            TaxConfig.tenant_id == tenant_id,
            TaxConfig.property_id == property_id,
        ),
    )


async def upsert_tax_config(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
    data: TaxConfigCreate,
) -> TaxConfig:
    existing = await get_tax_config(session, tenant_id, property_id)
    mode = TaxMode(data.tax_mode)
    name = data.tax_name.strip()
    if existing:
        existing.tax_mode = mode
        existing.tax_name = name
        existing.tax_rate = data.tax_rate
        await session.flush()
        await session.refresh(existing)
        return existing
    row = TaxConfig(
        tenant_id=tenant_id,
        property_id=property_id,
        tax_mode=mode,
        tax_name=name,
        tax_rate=data.tax_rate,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def delete_tax_config(
    session: AsyncSession,
    tenant_id: UUID,
    property_id: UUID,
) -> bool:
    row = await get_tax_config(session, tenant_id, property_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True
