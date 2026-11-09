import { describe, expect, it } from "vitest";
import type { ScenarioConfig } from "../api/types";
import { validateLogin } from "../pages/Login";
import { validateScenario } from "../pages/ScenarioBuilder";
import { validateDecision } from "../pages/StrategyDetail";
import { validateNewUser } from "../pages/admin/Users";

const base: ScenarioConfig = {
  name: "Test", description: "", start_year: 2025, end_year: 2040,
  demand: { basis: "forecast", extrapolation_growth_pct: null, efficiency_savings_pct_by_end: 0, demand_multiplier: 1 },
  constraints: { budget_total_musd: null, annual_budget_musd: null, renewable_share_targets: [], emission_reduction_targets: [], emission_caps_mt: [], max_gas_share: [], max_import_share_pct: null, reserve_margin_pct: null, max_vre_share_monthly_pct: null, technology_limits: [] },
  operating: { renewable_availability_multiplier: 1, gas_cost_multiplier: 1, capex_multipliers: {}, discount_rate: null, allow_imports: true },
  objective: { method: "single", primary: "min_cost", weights: {}, epsilon_bounds: {}, priorities: [], lexicographic_tolerance_pct: 1 },
};
const clone = () => structuredClone(base);

describe("scenario form validation", () => {
  it("accepts the default scenario", () => expect(validateScenario(base)).toEqual([]));
  it("rejects an inverted horizon", () => {
    const c = clone(); c.end_year = 2030; c.start_year = 2035;
    expect(validateScenario(c).join(" ")).toMatch(/End year/);
  });
  it("rejects targets outside the horizon and above 100 %", () => {
    const c = clone(); c.end_year = 2030;
    c.constraints.renewable_share_targets = [{ year: 2035, value: 120 }];
    const e = validateScenario(c).join(" ");
    expect(e).toMatch(/outside the horizon/);
    expect(e).toMatch(/0-100/);
  });
  it("rejects an annual budget above the total budget", () => {
    const c = clone(); c.constraints.budget_total_musd = 100; c.constraints.annual_budget_musd = 200;
    expect(validateScenario(c).join(" ")).toMatch(/Annual budget/);
  });
  it("checks multi-objective settings", () => {
    const c = clone(); c.objective.method = "weighted_sum"; c.objective.weights = { min_cost: 1 };
    expect(validateScenario(c).join(" ")).toMatch(/two positive weights/);
    c.objective.method = "lexicographic"; c.objective.priorities = ["min_cost"];
    expect(validateScenario(c).join(" ")).toMatch(/two distinct priorities/);
  });
});

describe("other forms", () => {
  it("validates login input", () => {
    expect(validateLogin("", "x")).toMatch(/username/);
    expect(validateLogin("admin", "")).toMatch(/password/);
    expect(validateLogin("admin", "secret")).toBeNull();
  });
  it("requires a rejection reason", () => {
    expect(validateDecision("reject", "no")).toMatch(/mandatory/);
    expect(validateDecision("reject", "Too expensive")).toBeNull();
    expect(validateDecision("approve", "")).toBeNull();
  });
  it("validates new user accounts", () => {
    expect(validateNewUser({ email: "bad", username: "abc", password: "Password123" })).toMatch(/email/);
    expect(validateNewUser({ email: "a@b.com", username: "abc", password: "short" })).toMatch(/Password/);
    expect(validateNewUser({ email: "a@b.com", username: "abc", password: "Password123" })).toBeNull();
  });
});
