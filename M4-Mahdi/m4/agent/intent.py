"""Planning intent: structured interpretation of a user's request.

Two interpreters produce the same `PlanningIntent` structure:
- `llm_parse`: the LLM extracts objectives/constraints as JSON (validated by Pydantic);
- `rule_parse`: deterministic regular-expression parser (fallback, always available).

Anti-fabrication rule: every numeric value extracted by the LLM must literally appear in
the user's text; otherwise it is discarded and reported. Missing values are never filled
silently — they become clarification questions or explicit assumptions to approve.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..scenarios.schema import DemandBasis, ObjectiveName
from .llm import LLMClient, LLMError

OBJECTIVE_KEYWORDS: dict[str, list[str]] = {
    "min_cost": [r"\bcost", r"cheap", r"afford", r"economic", r"least[- ]cost", r"inexpensive"],
    "min_emissions": [r"co2", r"\bemission", r"carbon", r"decarboni[sz]", r"climate", r"greenhouse", r"\bghg"],
    "min_dependence": [r"gas dependen", r"dependen\w* on gas", r"energy (?:independence|security)", r"\bimport",
                       r"reduce (?:the )?(?:use of )?(?:natural )?gas", r"less gas", r"independen"],
    "max_renewables": [r"renewable", r"\bsolar", r"\bwind", r"clean (?:energy|electricity)"],
    "min_investment": [r"minimi[sz]e (?:the )?(?:total )?investment", r"lowest investment"],
}
VAGUE_BUDGET = r"(limited|tight|small|restricted|constrained|modest|low|reduced)\s+(investment\s+)?budget|budget (constraint|limit)|limited (investment|funds|funding|money)"
NO_BUDGET = r"no budget (limit|constraint|cap)|unlimited budget|without (a )?budget (limit|constraint)|no (spending )?limit|^\s*(none|unlimited|no)\s*$"


class TargetSpec(BaseModel):
    year: int | None = None
    value: float
    year_inferred: bool = False  # True when the year was not attached to the target in the text


class PlanningIntent(BaseModel):
    objectives: list[ObjectiveName] = Field(default_factory=list)
    primary_objective: ObjectiveName | None = None
    start_year: int | None = None
    end_year: int | None = None
    budget_total_musd: float | None = None
    annual_budget_musd: float | None = None
    budget_mentioned_without_value: bool = False
    budget_unlimited_confirmed: bool = False
    renewable_targets: list[TargetSpec] = Field(default_factory=list)
    emission_reduction_targets: list[TargetSpec] = Field(default_factory=list)
    gas_share_limits: list[TargetSpec] = Field(default_factory=list)
    reserve_margin_pct: float | None = None
    reliability_mentioned: bool = False
    max_import_share_pct: float | None = None
    demand_case: DemandBasis | None = None
    efficiency_savings_pct: float | None = None
    notes: list[str] = Field(default_factory=list)
    parser: Literal["llm", "rules"] = "rules"


# ---------------------------------------------------------------------------------
_NUM = r"(\d+(?:[.,]\d+)?)"


def _num(s: str) -> float:
    return float(s.replace(",", ".")) if s.count(",") == 1 and "." not in s and len(s.split(",")[1]) != 3 \
        else float(s.replace(",", ""))


def _money_to_musd(value: float, unit: str | None) -> float:
    u = (unit or "").lower()
    if u in {"billion", "billions", "bn", "b", "bln", "md", "mds", "milliard", "milliards"}:
        return value * 1000.0
    if u in {"thousand", "k"}:
        return value / 1000.0
    return value  # million / M / MUSD / unspecified -> MUSD


def _year_near(text: str, start: int, end: int, default: int | None) -> tuple[int | None, bool]:
    """Year attached to a target -> (year, inferred?)."""
    clause_end = re.search(r"[,;.]|\band\b", text[end:])
    clause = text[end: end + (clause_end.start() if clause_end else 60)]
    after = re.search(r"(?:by|in|until|before|from|for|after)\s+(20\d\d)", text[end: end + 60])
    if after and (after.start() <= len(clause) + 25):
        return int(after.group(1)), False
    before = re.search(r"(?:by|in|until|from)\s+(20\d\d)[^.;]{0,25}$", text[max(0, start - 40): start])
    if before:
        return int(before.group(1)), False
    years = re.findall(r"20\d\d", text[max(0, start - 60): end + 60])
    return (int(years[-1]) if years else default), True


def rule_parse(text: str) -> PlanningIntent:
    t = " " + text.lower().replace("’", "'") + " "
    intent = PlanningIntent(parser="rules")

    # horizon -------------------------------------------------------------
    years = [int(y) for y in re.findall(r"\b(20[2-6]\d)\b", t)]
    m = re.search(r"(?:from|between)\s+(20\d\d)\s*(?:to|and|-|–|until)\s*(20\d\d)", t)
    if m:
        intent.start_year, intent.end_year = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(?:by|until|horizon|through|to)\s+(20\d\d)", t)
        if m:
            intent.end_year = max(int(m.group(1)), max(years) if years else 0)
        elif years:
            intent.end_year = max(years)

    # objectives ------------------------------------------------------------
    hits = []
    for obj, pats in OBJECTIVE_KEYWORDS.items():
        pos = [mm.start() for p in pats for mm in re.finditer(p, t)]
        if pos:
            hits.append((min(pos), obj))
    if re.search(VAGUE_BUDGET, t) or re.search(r"\bbudget\b", t):
        # a budget is a constraint, not an objective; "cost" may still be an objective
        hits = [(p, o) for p, o in hits if not (o == "min_cost" and not re.search(r"\bcost", t))]
    intent.objectives = [o for _, o in sorted(hits)]
    if "min_investment" in intent.objectives and re.search(r"\bcost", t) is None:
        pass
    intent.primary_objective = intent.objectives[0] if intent.objectives else None

    # budget ---------------------------------------------------------------
    money = re.finditer(
        r"(?:\$|usd\s*|musd\s*)?" + _NUM + r"\s*(billion|billions|bn|bln|b|million|millions|m|musd|md|mds|k|thousand)?"
        r"\s*(?:usd|us\$|\$|dollars|musd|of dollars)?", t)
    for mm in money:
        val, unit = mm.group(1), mm.group(2)
        ctx = t[max(0, mm.start() - 50): mm.end() + 30]
        if not re.search(r"budget|invest|spend|capex|fund", ctx):
            continue
        if re.fullmatch(r"20\d\d", val) or "%" in t[mm.end(): mm.end() + 2]:
            continue
        if unit is None and not re.search(r"\$|usd|musd|dollar", t[max(0, mm.start() - 6): mm.end() + 12]):
            continue
        musd = _money_to_musd(_num(val), unit)
        tail = re.split(r"[,;.]|\d", t[mm.end():], maxsplit=1)[0][:25]
        if re.search(r"per year|annual|/year|a year|each year|yearly|/yr|per annum", tail) \
                or re.search(r"annual", t[max(0, mm.start() - 25): mm.start()]):
            intent.annual_budget_musd = musd
        else:
            intent.budget_total_musd = musd
    if intent.budget_total_musd is None and intent.annual_budget_musd is None:
        if re.search(NO_BUDGET, t.strip()) or re.search(NO_BUDGET, t):
            intent.budget_unlimited_confirmed = True
        elif re.search(VAGUE_BUDGET, t) or re.search(r"\bbudget\b", t):
            intent.budget_mentioned_without_value = True

    # percentage targets -------------------------------------------------------
    for mm in re.finditer(_NUM + r"\s*(?:%|percent)", t):
        val = _num(mm.group(1))
        before, after = t[max(0, mm.start() - 70): mm.start()], t[mm.end(): mm.end() + 45]
        yr, inferred = _year_near(t, mm.start(), mm.end(), intent.end_year)
        if re.search(r"reserve margin|reserve", before[-40:]) or re.search(r"^\s*reserve", after):
            intent.reserve_margin_pct = val
        elif re.search(r"import", before[-35:] + after[:25]) and not re.search(r"renewable", after[:25]):
            intent.max_import_share_pct = val
        elif re.search(r"(co2|emission|carbon|ghg)", after[:40] + before[-40:]) and \
                re.search(r"reduc|cut|lower|decreas|below|less", before + after):
            intent.emission_reduction_targets.append(TargetSpec(year=yr, value=val, year_inferred=inferred))
        elif re.search(r"renewable|clean|green|\bre\b", after[:40] + before[-30:]):
            intent.renewable_targets.append(TargetSpec(year=yr, value=val, year_inferred=inferred))
        elif re.search(r"\bgas\b", before[-40:] + after[:30]):
            intent.gas_share_limits.append(TargetSpec(year=yr, value=val, year_inferred=inferred))
        elif re.search(r"efficien|demand reduction|save", before[-40:] + after[:30]):
            intent.efficiency_savings_pct = val

    if re.search(r"reliab|security of supply|reserve margin|blackout|adequacy", t):
        intent.reliability_mentioned = True
    if re.search(r"high(er)? demand|demand grows faster|faster demand|stress", t):
        intent.demand_case = "upper_80"
    elif re.search(r"low(er)? demand|slower demand", t):
        intent.demand_case = "lower_80"
    for kw, note in ((r"nuclear", "Nuclear power is not modelled in this version."),
                     (r"batter|storage", "Storage (batteries, pumped hydro) is not modelled in this version."),
                     (r"hydrogen", "Hydrogen is not modelled in this version.")):
        if re.search(kw, t):
            intent.notes.append(note)
    return intent


def parse_money_any(text: str) -> float | None:
    """Parse a bare amount (used when the user answers a budget question, e.g. '9000 MUSD')."""
    t = text.lower()
    m = re.search(r"(?:\$|usd\s*)?" + _NUM + r"\s*(billion|billions|bn|bln|b|million|millions|m|musd|md|mds)?\b", t)
    if not m or re.fullmatch(r"20\d\d", m.group(1)):
        return None
    return _money_to_musd(_num(m.group(1)), m.group(2))


# ---------------------------------------------------------------------------------
LLM_SYSTEM = """You extract energy-planning intents for Tunisia's electricity system.
Return ONLY a JSON object with these keys:
objectives (list of: min_cost, min_emissions, min_dependence, max_renewables, min_investment, ordered by importance),
start_year, end_year, budget_total_musd, annual_budget_musd (millions of USD; 1 billion = 1000),
budget_mentioned_without_value (true if a budget limit is mentioned without a number),
budget_unlimited_confirmed (true only if the user explicitly says there is no budget limit),
renewable_targets, emission_reduction_targets (% reduction vs 2024), gas_share_limits (% max share)
  — each a list of {"year": int|null, "value": number},
reserve_margin_pct, reliability_mentioned, max_import_share_pct,
demand_case (forecast | upper_80 | lower_80 | upper_95 | lower_95 | null), efficiency_savings_pct, notes (list of strings).
Rules: NEVER invent numbers. Use null when a value is not stated explicitly. Do not compute anything."""


def _numbers_in(text: str) -> set[float]:
    out = set()
    for s in re.findall(r"\d+(?:[.,]\d+)?", text):
        try:
            out.add(round(_num(s), 6))
        except ValueError:
            continue
    return out


def llm_parse(text: str, client: LLMClient) -> tuple[PlanningIntent, list[str]]:
    """Returns (intent, warnings). Raises LLMError if the LLM is unavailable or unusable."""
    data = client.json_completion(LLM_SYSTEM, text)
    data["parser"] = "llm"
    data = {k: v for k, v in data.items() if k in PlanningIntent.model_fields}
    for k in ("renewable_targets", "emission_reduction_targets", "gas_share_limits", "objectives", "notes"):
        if data.get(k) is None:
            data[k] = []
    try:
        intent = PlanningIntent.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"LLM intent failed validation: {exc.errors()[0]['msg']}") from exc
    warnings = []
    present = _numbers_in(text)

    def grounded(v: float) -> bool:
        return any(abs(v - p) < 1e-6 or abs(v - p * 1000) < 1e-6 or abs(v * 1000 - p) < 1e-6 for p in present)

    for f in ("budget_total_musd", "annual_budget_musd", "reserve_margin_pct", "max_import_share_pct",
              "efficiency_savings_pct"):
        v = getattr(intent, f)
        if v is not None and not grounded(v):
            warnings.append(f"Discarded LLM value {f}={v}: not stated in the request.")
            setattr(intent, f, None)
    for f in ("renewable_targets", "emission_reduction_targets", "gas_share_limits"):
        kept = [x for x in getattr(intent, f) if grounded(x.value)]
        if len(kept) != len(getattr(intent, f)):
            warnings.append(f"Discarded ungrounded LLM targets in {f}.")
        setattr(intent, f, kept)
    for f in ("start_year", "end_year"):
        v = getattr(intent, f)
        if v is not None and float(v) not in present:
            warnings.append(f"Discarded LLM value {f}={v}: not stated in the request.")
            setattr(intent, f, None)
    if intent.objectives and not intent.primary_objective:
        intent.primary_objective = intent.objectives[0]
    return intent, warnings


def interpret(text: str, client: LLMClient | None = None) -> tuple[PlanningIntent, list[str]]:
    client = client or LLMClient()
    if client.available:
        try:
            return llm_parse(text, client)
        except LLMError as exc:
            intent = rule_parse(text)
            return intent, [f"LLM interpretation failed ({exc}); deterministic parser used instead."]
    return rule_parse(text), []


def merge_intents(old: PlanningIntent | None, new: PlanningIntent) -> PlanningIntent:
    """Values stated in the newest message override earlier ones; lists are replaced when restated."""
    if old is None:
        return new
    merged = old.model_copy(deep=True)
    for f in ("start_year", "end_year", "budget_total_musd", "annual_budget_musd", "reserve_margin_pct",
              "max_import_share_pct", "demand_case", "efficiency_savings_pct"):
        v = getattr(new, f)
        if v is not None:
            setattr(merged, f, v)
    for f in ("renewable_targets", "emission_reduction_targets", "gas_share_limits"):
        if getattr(new, f):
            setattr(merged, f, getattr(new, f))
    if new.objectives:
        merged.objectives = list(dict.fromkeys(new.objectives + [o for o in merged.objectives if o not in new.objectives]))
        merged.primary_objective = new.primary_objective or new.objectives[0]
    if new.budget_total_musd is not None or new.annual_budget_musd is not None or new.budget_unlimited_confirmed:
        merged.budget_mentioned_without_value = False
    if new.budget_unlimited_confirmed:
        merged.budget_unlimited_confirmed = True
        merged.budget_total_musd = None
        merged.annual_budget_musd = None
    elif new.budget_mentioned_without_value and merged.budget_total_musd is None and merged.annual_budget_musd is None:
        merged.budget_mentioned_without_value = True
    merged.reliability_mentioned = merged.reliability_mentioned or new.reliability_mentioned
    merged.notes = list(dict.fromkeys(merged.notes + new.notes))
    merged.parser = new.parser
    return merged
