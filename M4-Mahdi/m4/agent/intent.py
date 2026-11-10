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

from ..operations.schema import OpObjective
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


OP_OBJECTIVE_KEYWORDS: dict[str, list[str]] = {
    "min_operating_cost": [r"\bcost", r"cheap", r"econom", r"expens", r"spend"],
    "min_unserved": [r"satisf\w* (?:the )?demand", r"meet(?:ing)? (?:the )?demand", r"unserved", r"unmet", r"shortage",
                     r"blackout", r"load shedding", r"reliab", r"security of supply", r"cover (?:the )?demand"],
    "min_gas": [r"\bgas (?:use|consumption)", r"\bfuel", r"resource consumption", r"consum\w* less", r"save gas", r"less gas"],
    "min_emissions": [r"co2", r"\bemission", r"carbon"],
}
OPERATIONAL_PATTERNS = [
    r"next (?:month|quarter|summer|winter|season|\d+ months|(?:two|three|four|five|six|few|couple of) months)",
    r"(?:coming|upcoming) (?:month|months|weeks|summer|winter)", r"this (?:month|summer|winter)", r"short[- ]term",
    r"\boperational\b", r"operating cost", r"production capacity", r"\bdispatch", r"unit commitment",
    r"units? (?:in service|online|available)", r"\boutage", r"maintenance", r"plants? (?:are )?(?:unavailable|offline|down)",
    r"\boperat(?:e|es|ion|ions|ing)\b",
    r"(?:in|for|during|from)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+20\d\d",
]
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "couple of": 2, "few": 3}
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"])}
TECH_WORDS = {"gas": "gas", "thermal": "gas", "ccgt": "gas", "solar": "solar", "pv": "solar", "wind": "wind", "hydro": "hydro"}


class CapacityOutageSpec(BaseModel):
    technology: str
    unavailable_mw: float | None = None
    reduction_pct: float | None = None


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
    # --- short-term operational problems ---------------------------------------------
    problem_type: Literal["planning", "operational"] = "planning"
    op_objectives: list[OpObjective] = Field(default_factory=list)
    period_start: str | None = None              # YYYY-MM
    period_months: int | None = None
    period_relative: str | None = None           # e.g. "next month" (resolved against the M1 data)
    demand_change_pct: float | None = None
    demand_increase_mentioned: bool = False
    capacity_outages: list[CapacityOutageSpec] = Field(default_factory=list)
    capacity_limit_mentioned_without_value: bool = False
    gas_limit_gwh: float | None = None
    gas_limit_mentioned_without_value: bool = False


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
    _rule_parse_operational(t, intent)
    return intent


def _rule_parse_operational(t: str, intent: PlanningIntent) -> None:
    """Detect short-term operational problems (operate the existing fleet over a few months)."""
    long_term = re.search(r"(?:by|until|horizon|through|to|in)\s+20(?:2[8-9]|[3-6]\d)\b", t) or re.search(
        r"\b(?:invest|investment|build|new capacity|expansion|roadmap|long[- ]term|transition)", t)
    if not any(re.search(p, t) for p in OPERATIONAL_PATTERNS) or long_term:
        return
    intent.problem_type = "operational"
    intent.objectives, intent.primary_objective = [], None   # planning objectives do not apply
    intent.end_year = intent.start_year = None
    intent.budget_mentioned_without_value = False
    # objectives (ordered by first mention)
    hits = []
    for obj, pats in OP_OBJECTIVE_KEYWORDS.items():
        pos = [mm.start() for p in pats for mm in re.finditer(p, t)]
        if pos:
            hits.append((min(pos), obj))
    intent.op_objectives = [o for _, o in sorted(hits)]
    # period
    m = re.search(r"next (\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|couple of|few) months", t)
    if m:
        intent.period_months = int(m.group(1)) if m.group(1).isdigit() else WORD_NUM[m.group(1)]
        intent.period_relative = f"next {intent.period_months} months"
    elif re.search(r"next quarter", t):
        intent.period_months, intent.period_relative = 3, "next quarter"
    elif re.search(r"(?:next|coming|upcoming|this) month", t):
        intent.period_months, intent.period_relative = 1, "next month"
    mm = re.search(r"(?:in|for|during|from)\s+(" + "|".join(MONTHS) + r")\s+(20\d\d)", t)
    if mm:
        intent.period_start = f"{mm.group(2)}-{MONTHS[mm.group(1)]:02d}"
        intent.period_relative = None
        rng = re.search(r"(?:to|until|through|-)\s+(" + "|".join(MONTHS) + r")(?:\s+(20\d\d))?", t[mm.end(): mm.end() + 30])
        if rng:
            y2 = int(rng.group(2) or mm.group(2))
            n = (y2 - int(mm.group(2))) * 12 + MONTHS[rng.group(1)] - MONTHS[mm.group(1)] + 1
            if 1 <= n <= 24:
                intent.period_months = n
        intent.period_months = intent.period_months or 1
    elif re.search(r"(?:next|coming|upcoming|this) summer", t):
        intent.period_relative, intent.period_months = "next summer (June-August)", 3
    elif re.search(r"(?:next|coming|upcoming|this) winter", t):
        intent.period_relative, intent.period_months = "next winter (December-February)", 3
    # demand change
    d = re.search(r"demand[^.;%]{0,40}?(?:increas\w*|ris\w*|grow\w*|up|higher)\s*(?:by\s*)?" + _NUM + r"\s*(?:%|percent)", t) \
        or re.search(_NUM + r"\s*(?:%|percent)\s*(?:higher|more|increase|rise)[^.;]{0,20}demand", t)
    dd = re.search(r"demand[^.;%]{0,40}?(?:decreas\w*|fall\w*|drop\w*|lower)\s*(?:by\s*)?" + _NUM + r"\s*(?:%|percent)", t)
    if d:
        intent.demand_change_pct = _num(d.group(1))
    elif dd:
        intent.demand_change_pct = -_num(dd.group(1))
    elif re.search(r"demand[^.;]{0,30}(?:increas|ris|grow|higher|peak)|(?:increas|ris|grow|higher)\w*[^.;]{0,20}demand", t):
        intent.demand_increase_mentioned = True
    # capacity outages
    tech = r"(gas|thermal|ccgt|solar|pv|wind|hydro)"
    found = False
    for mo in re.finditer(_NUM + r"\s*mw\s*(?:of\s*)?(?:the\s*)?" + tech + r"[^.;]{0,40}?(?:unavailable|out of service|outage|maintenance|offline|down|lost)", t):
        intent.capacity_outages.append(CapacityOutageSpec(technology=TECH_WORDS[mo.group(2)], unavailable_mw=_num(mo.group(1))))
        found = True
    for mo in re.finditer(tech + r"[^.;]{0,40}?(?:unavailable|out of service|outage|maintenance|offline|down|lost)[^.;]{0,20}?" + _NUM + r"\s*mw", t):
        if not any(o.technology == TECH_WORDS[mo.group(1)] for o in intent.capacity_outages):
            intent.capacity_outages.append(CapacityOutageSpec(technology=TECH_WORDS[mo.group(1)], unavailable_mw=_num(mo.group(2))))
            found = True
    for mo in re.finditer(tech + r"\s*capacity[^.;]{0,25}?(?:reduced|down|lower|cut|decreas\w*)\s*(?:by\s*)?" + _NUM + r"\s*(?:%|percent)", t):
        intent.capacity_outages.append(CapacityOutageSpec(technology=TECH_WORDS[mo.group(1)], reduction_pct=_num(mo.group(2))))
        found = True
    if not found and re.search(r"(?:limited|reduced|constrained|insufficient|tight)\s+(?:production |generation |generating )?capacity|capacity (?:is )?(?:limited|constrained|insufficient)", t):
        intent.capacity_limit_mentioned_without_value = True
    # gas fuel availability
    g = re.search(r"(?:gas|fuel)[^.;]{0,40}?(?:limit\w*|available|supply|at most|maximum|max)[^.;]{0,15}?" + _NUM + r"\s*gwh", t)
    if g:
        intent.gas_limit_gwh = _num(g.group(1))
    elif re.search(r"(?:limited|restricted|constrained|reduced) (?:natural )?(?:gas|fuel) (?:supply|availability|deliveries)|(?:gas|fuel) (?:supply|availability) (?:is )?(?:limited|restricted|constrained)", t):
        intent.gas_limit_mentioned_without_value = True
    if re.search(r"reliab|security of supply|reserve|blackout", t):
        intent.reliability_mentioned = True


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
demand_case (forecast | upper_80 | lower_80 | upper_95 | lower_95 | null), efficiency_savings_pct, notes (list of strings),
problem_type: "operational" for short-term operation of the EXISTING fleet over a few months (dispatch, units in service,
  outages, operating cost, next month...), otherwise "planning" (long-term investment, targets by a future year),
op_objectives (operational only; list of: min_operating_cost, min_unserved (satisfy demand / reliability),
  min_gas (fuel / resource consumption), min_emissions; ordered by importance),
demand_change_pct (stated % change of demand, negative for a decrease), demand_increase_mentioned (true if an increase is
mentioned without a number), capacity_outages (list of {"technology": gas|solar|wind|hydro, "unavailable_mw": number|null,
"reduction_pct": number|null}), capacity_limit_mentioned_without_value, gas_limit_gwh, gas_limit_mentioned_without_value.
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
    data = client.json_completion(LLM_SYSTEM, text, purpose="intent")
    data["parser"] = "llm"
    data = {k: v for k, v in data.items() if k in PlanningIntent.model_fields and v is not None
            or k in ("budget_total_musd", "annual_budget_musd")}
    for k in ("budget_mentioned_without_value", "budget_unlimited_confirmed", "reliability_mentioned",
              "demand_increase_mentioned", "capacity_limit_mentioned_without_value", "gas_limit_mentioned_without_value"):
        data[k] = bool(data.get(k) or False)
    if data.get("problem_type") not in ("planning", "operational"):
        data.pop("problem_type", None)
    for k in ("renewable_targets", "emission_reduction_targets", "gas_share_limits", "objectives", "notes",
              "op_objectives", "capacity_outages"):
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
    for f in ("demand_change_pct", "gas_limit_gwh"):
        v = getattr(intent, f)
        if v is not None and not grounded(abs(v)):
            warnings.append(f"Discarded LLM value {f}={v}: not stated in the request.")
            setattr(intent, f, None)
    kept = []
    for o in intent.capacity_outages:
        vals = [x for x in (o.unavailable_mw, o.reduction_pct) if x is not None]
        if o.technology in TECH_WORDS.values() and vals and all(grounded(x) for x in vals):
            kept.append(o)
        else:
            warnings.append(f"Discarded LLM capacity outage {o.model_dump()}: value or technology not stated in the request.")
    intent.capacity_outages = kept
    # the operating period is resolved deterministically (relative expressions such as "next month")
    rules = rule_parse(text)
    if intent.problem_type == "operational" or rules.problem_type == "operational":
        intent.problem_type = "operational"
        intent.objectives, intent.primary_objective = [], None
        intent.period_start, intent.period_months, intent.period_relative = \
            rules.period_start, rules.period_months, rules.period_relative
        if not intent.op_objectives:
            intent.op_objectives = rules.op_objectives
        if not intent.capacity_outages and rules.capacity_outages:
            intent.capacity_outages = rules.capacity_outages
        intent.capacity_limit_mentioned_without_value = intent.capacity_limit_mentioned_without_value and not intent.capacity_outages
        intent.budget_mentioned_without_value = False
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
    # operational fields
    if new.problem_type == "operational" and old.problem_type != "operational":
        merged.problem_type = "operational"
        merged.objectives, merged.primary_objective = [], None
    for f in ("period_start", "period_months", "period_relative", "demand_change_pct", "gas_limit_gwh"):
        v = getattr(new, f)
        if v is not None:
            setattr(merged, f, v)
    if new.op_objectives:
        merged.op_objectives = list(dict.fromkeys(new.op_objectives + [o for o in merged.op_objectives if o not in new.op_objectives]))
    if new.capacity_outages:
        by = {o.technology: o for o in merged.capacity_outages}
        by.update({o.technology: o for o in new.capacity_outages})
        merged.capacity_outages = list(by.values())
        merged.capacity_limit_mentioned_without_value = False
    if new.demand_change_pct is not None:
        merged.demand_increase_mentioned = False
    if new.gas_limit_gwh is not None:
        merged.gas_limit_mentioned_without_value = False
    merged.demand_increase_mentioned = merged.demand_increase_mentioned or (
        new.demand_increase_mentioned and merged.demand_change_pct is None)
    merged.capacity_limit_mentioned_without_value = merged.capacity_limit_mentioned_without_value or (
        new.capacity_limit_mentioned_without_value and not merged.capacity_outages)
    merged.gas_limit_mentioned_without_value = merged.gas_limit_mentioned_without_value or (
        new.gas_limit_mentioned_without_value and merged.gas_limit_gwh is None)
    return merged
