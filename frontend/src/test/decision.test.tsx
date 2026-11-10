import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { DecisionReport, FormulationValidation, StrategySummary } from "../api/types";
import { DecisionReportView, FormulationChecks } from "../components/decision";
import { QuestionsForm } from "../pages/PlanningAgent";

const report: DecisionReport = {
  problem_type: "operational",
  understanding: null,
  data_used: [{ source: "m1_demand_forecast", data_status: "MOCK", dataset_version: "v1", path: "m1.csv", used_for: "demand" }],
  investigation_tools: ["get_monthly_demand"],
  assumptions: [],
  formulation: { problem_class: "MILP", objective: "min cost", n_decision_variables: 7, constraints: ["balance"], strategies: [] },
  computation: { solver: "HiGHS", solves: 6, solver_seconds: 0.05, n_strategies: 2, n_feasible: 2, n_distinct: 2, all_validated: true, status: "ok" },
  options: [
    { key: "least_cost_operations", name: "Least-cost operation", objective: "cost", metrics: { operating_cost_musd: 116.8, gas_gwh: 1501 }, advantages: ["Lowest cost"], disadvantages: [], risks: [] },
    { key: "min_gas_use", name: "Minimum gas consumption", objective: "gas", metrics: { operating_cost_musd: 119, gas_gwh: 1350 }, advantages: [], disadvantages: ["Higher cost"], risks: [] },
  ],
  recommendation: { strategy_key: "least_cost_operations", strategy_name: "Least-cost operation", reason: "Your first priority is to minimize operating cost.", statement: "The final decision is yours.", priority_basis: ["min_operating_cost"], alternatives: [] },
  limitations: [],
  narrative: { source: "template", text: "Paragraph one.\n\nParagraph two." },
  decision_owner: "human",
};
const strat = (id: string, key: string): StrategySummary => ({
  id, key, planning_request_id: "r", owner_id: 1, run_id: null, name: key, strategic_objective: "", version: 1, parent_strategy_id: null,
  status: "pending_review", is_reference: false, feasible: true, duplicate_of: null, superseded_by: null, data_status: "MOCK", created_at: "", headline: null,
});

describe("decision support", () => {
  it("shows the recommendation as conditional and lets the user record a preferred option", async () => {
    const onSelect = vi.fn();
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><DecisionReportView report={report} strategies={[strat("s1", "least_cost_operations"), strat("s2", "min_gas_use")]} selection={null} canSelect busy={false} onSelect={onSelect} /></MemoryRouter>);
    expect(screen.getByText(/Recommended under your stated priorities: Least-cost operation/)).toBeInTheDocument();
    expect(screen.getByText(/final decision is yours/)).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: /Prefer this option/ });
    await userEvent.click(buttons[1]);
    expect(onSelect).toHaveBeenCalledWith("s2");
  });

  it("marks the requester's choice and hides selection when not allowed", () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><DecisionReportView report={report} strategies={[strat("s1", "least_cost_operations"), strat("s2", "min_gas_use")]}
      selection={{ strategy_id: "s2", strategy_key: "min_gas_use", strategy_name: "Minimum gas consumption", by: "u", comment: "", follows_recommendation: false, at: "" }}
      canSelect={false} busy={false} onSelect={() => undefined} /></MemoryRouter>);
    expect(screen.getByText("YOUR CHOICE")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Prefer this option/ })).toBeNull();
    expect(screen.getByText(/differs from the recommendation/)).toBeInTheDocument();
  });

  it("renders formulation checks including a failed feasibility pre-check", () => {
    const v: FormulationValidation = { status: "fail", feasibility: { feasible: false, status: "infeasible", solver_seconds: 0.1 },
      checks: [{ check: "Units consistent", status: "pass", detail: "ok" }, { check: "Feasibility pre-check", status: "fail", detail: "reserve cannot be met" }] };
    render(<FormulationChecks v={v} />);
    expect(screen.getByText("Feasibility pre-check")).toBeInTheDocument();
    expect(screen.getByText("reserve cannot be met")).toBeInTheDocument();
  });

  it("single-choice questions accept an option or a custom percentage, and reject invalid text", async () => {
    const onSubmit = vi.fn();
    render(<QuestionsForm busy={false} onSubmit={onSubmit} questions={[{ id: "demand_scenario", required: true, type: "single_select", question: "Which demand?", unit: "%",
      allow_number: true, options: [{ value: "forecast", label: "M1 point forecast (1,650 GWh)" }, { value: "upper_80", label: "Upper bound" }] }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Submit answers" }));
    expect(onSubmit).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText(/custom value/), "abc");
    await userEvent.click(screen.getByRole("button", { name: "Submit answers" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not a valid percentage/);
    await userEvent.click(screen.getByLabelText(/M1 point forecast/));
    await userEvent.click(screen.getByRole("button", { name: "Submit answers" }));
    expect(onSubmit).toHaveBeenCalledWith({ demand_scenario: "forecast" });
  });
});
