// Types mirroring the backend JSON payloads (see M4-Mahdi/docs/openapi.json).

export type Role = "USER" | "ADMIN";
export type DataStatus = "MOCK" | "REAL" | "MIXED" | "UNKNOWN";

export interface User {
  id: number;
  email: string;
  username: string;
  full_name: string;
  role: Role;
  is_active: boolean;
}

export interface Provenance {
  source: string;
  path: string;
  data_status: DataStatus;
  dataset_version: string;
  producer: string;
  description: string;
  sha256: string;
  rows: number;
  adapter: string;
  transformations: string[];
  warnings: string[];
}

export type TechMap = Record<string, number>;

export interface AnnualResult {
  year: number;
  demand_gwh: number;
  supply_requirement_gwh: number;
  generation_gwh: TechMap;
  imports_gwh: number;
  unserved_gwh: number;
  curtailment_gwh: TechMap;
  renewable_share: number;
  gas_share: number;
  import_share: number;
  dependence_share: number;
  emissions_mt: number;
  capacity_mw: TechMap;
  new_capacity_mw: TechMap;
  firm_capacity_mw: number;
  peak_demand_mw: number;
  reserve_margin_achieved: number;
  investment_musd: number;
  annualized_capital_musd: number;
  fixed_om_musd: number;
  variable_cost_musd: number;
  import_cost_musd: number;
  total_cost_musd: number;
  discounted_cost_musd: number;
}

export interface Summary {
  total_cost_npv_musd: number;
  total_investment_musd: number;
  total_emissions_mt: number;
  final_year: number;
  final_year_emissions_mt: number;
  emission_change_vs_2024_pct: number | null;
  renewable_share_final_year: number;
  renewable_share_horizon: number;
  gas_share_final_year: number;
  import_share_horizon: number;
  dependence_share_final_year: number;
  dependence_share_horizon: number;
  total_new_capacity_mw: TechMap;
  final_capacity_mw: TechMap;
  total_unserved_gwh: number;
  total_curtailment_gwh: number;
  min_reserve_margin: number;
  baseline_2024_emissions_mt: number;
  objective_values?: Record<string, number>;
}

export interface ConstraintRow {
  constraint: string;
  scope: string;
  value: number;
  limit: number;
  relation: string;
  unit: string;
  satisfied: boolean;
  margin: number;
}

export interface Validation {
  status: "valid" | "invalid";
  physical_checks: { check: string; passed: boolean; [k: string]: unknown }[];
  constraints: ConstraintRow[];
  objective_consistency: Record<string, { solver_expression: number; recomputed: number; consistent: boolean }>;
  simulation_check: { method: string; unserved_gwh: number; adequate: boolean; issues: string[] };
}

export interface Formulation {
  problem_class: string;
  horizon: string;
  method: string;
  method_explanation: string;
  objective: string;
  decision_variables: { symbol: string; unit: string; meaning: string; count: number }[];
  constraints: { name: string; expression: string; values: string }[];
  assumptions: string[];
  data_status: DataStatus;
}

export interface SolverInfo {
  name: string | null;
  model_build_seconds?: number;
  solves: { step: string; status: string; termination: string; seconds: number; message: string }[];
}

export interface RunResult {
  status: string;
  feasible: boolean;
  method: string;
  message?: string;
  scenario: ScenarioConfig;
  formulation?: Formulation;
  data_status: DataStatus;
  provenance: Provenance[];
  assumptions: string[];
  demand_meta?: Record<string, unknown>;
  baseline_2024?: Record<string, unknown>;
  solver?: SolverInfo;
  payoff_table?: { rows: Record<string, Record<string, number>>; utopia: Record<string, number>; nadir: Record<string, number> } | null;
  notes?: string[];
  results: { annual: AnnualResult[]; summary: Summary; objective_values: Record<string, number>; adequacy_issues?: string[] } | null;
  objective_values?: Record<string, number> | null;
  validation?: Validation | null;
  diagnostics?: { status: string; summary: string; violations: { constraint: string; title: string; total_shortfall: number; unit: string; years: number[] }[] };
  points?: ParetoPoint[];
  primary?: string;
  secondary?: string;
}

export interface ParetoPoint {
  epsilon: number;
  status: string;
  objective_values?: Record<string, number>;
  renewable_share_final_year?: number;
  total_investment_musd?: number;
}

export interface RunSummary {
  id: string;
  kind: "optimization" | "simulation" | "pareto" | "operational";
  scenario_id: string | null;
  scenario_name: string;
  owner_id: number | null;
  planning_request_id: string | null;
  status: string;
  feasible: boolean;
  method: string;
  data_status: DataStatus;
  summary: Summary | null;
  duration_seconds: number;
  created_at: string;
}

export interface RunDetail extends RunSummary {
  result: RunResult;
}

export interface YearValue {
  year: number;
  value: number;
}

export interface TechnologyLimit {
  technology: string;
  allow_investment: boolean;
  max_annual_build_mw: number | null;
  max_total_new_mw: number | null;
  min_total_new_mw: number | null;
}

export interface ScenarioConfig {
  name: string;
  description: string;
  start_year: number;
  end_year: number;
  demand: {
    basis: string;
    extrapolation_growth_pct: number | null;
    efficiency_savings_pct_by_end: number;
    demand_multiplier: number;
  };
  constraints: {
    budget_total_musd: number | null;
    annual_budget_musd: number | null;
    renewable_share_targets: YearValue[];
    emission_reduction_targets: YearValue[];
    emission_caps_mt: YearValue[];
    max_gas_share: YearValue[];
    max_import_share_pct: number | null;
    reserve_margin_pct: number | null;
    max_vre_share_monthly_pct: number | null;
    technology_limits: TechnologyLimit[];
  };
  operating: {
    renewable_availability_multiplier: number;
    gas_cost_multiplier: number;
    capex_multipliers: Record<string, number>;
    discount_rate: number | null;
    allow_imports: boolean;
  };
  objective: {
    method: string;
    primary: string;
    weights: Record<string, number>;
    epsilon_bounds: Record<string, { mode: string; value: number }>;
    priorities: string[];
    lexicographic_tolerance_pct: number;
  };
}

export interface Scenario {
  id: string;
  name: string;
  description: string;
  is_predefined: boolean;
  owner_id: number | null;
  config: ScenarioConfig;
  created_at: string;
}

export interface Question {
  id: string;
  required: boolean;
  type: string;
  question: string;
  unit?: string;
  why?: string;
  context?: string;
  proposed_value?: number | null;
  proposal_source?: string;
  options?: { value: string; label: string }[];
  allow_number?: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant" | "admin";
  content: string;
  kind: string;
  data?: Record<string, unknown> | null;
  at: string;
}

export interface TraceStep {
  node: string;
  status: string;
  summary: string;
  tools: string[];
  seconds: number;
  at: string;
}

export interface PlanningRequest {
  id: string;
  title: string;
  status: string;
  owner_id: number;
  owner_email: string | null;
  created_at: string;
  updated_at: string;
  strategy_ids: string[];
  messages?: ChatMessage[];
  trace?: TraceStep[];
  intent?: Record<string, unknown>;
  questions?: Question[];
  investigation?: {
    planner: string;
    tool_calls: { tool: string; args: Record<string, unknown>; status: string; error?: string }[];
    findings: string[];
    missing_information: string[];
    required_assumptions: string[];
    data_status: DataStatus;
  };
  scenario?: ScenarioConfig;
  formulation?: Formulation;
  strategy_plan?: { key: string; name: string; objective: string; method: string }[];
  assumptions_to_approve?: string[];
  tradeoffs?: { table: Record<string, unknown>[] };
  warnings?: string[];
  llm?: { configured: boolean; model: string | null; fallback_models?: string[] };
  approval?: { approved: boolean; by: string; comment: string; at: string };
  admin_feedback?: { strategy_id: string; comment: string; by: string; at: string };
  problem_type?: ProblemType;
  understanding?: Understanding | null;
  formulation_validation?: FormulationValidation | null;
  decision_report?: DecisionReport | null;
  user_selection?: UserSelection | null;
  llm_usage?: LlmUsage;
  llm_calls?: { purpose: string; model_used: string | null; status: string; total_tokens: number | null; seconds: number | null; error: string | null }[];
}

export type ProblemType = "planning" | "operational";

export interface Understanding {
  problem_type: ProblemType;
  problem_type_label: string;
  objectives: string[];
  decision_variables: string[];
  resources: string[];
  constraints: string[];
  time_horizon: string;
  relevant_data: string[];
  missing_information: string[];
  interpreted_by: string;
}

export interface FormulationValidation {
  status: "pass" | "warn" | "fail";
  checks: { check: string; status: "pass" | "warn" | "fail"; detail: string }[];
  feasibility: { feasible: boolean; status: string; solver_seconds: number; max_achievable_reserve_margin?: number; diagnostics?: { summary: string } };
}

export interface Recommendation {
  strategy_key: string;
  strategy_name: string;
  strategy_id?: string | null;
  reason: string;
  statement: string;
  priority_basis: string[];
  alternatives: { criterion: string; strategy_key: string; strategy_name: string; value: number; unit: string }[];
}

export interface DecisionReport {
  problem_type: ProblemType;
  understanding: Understanding | null;
  data_used: { source: string; data_status: DataStatus; dataset_version: string; path: string; used_for: string }[];
  investigation_tools: string[];
  assumptions: string[];
  formulation: { problem_class: string; objective: string; n_decision_variables: number; constraints: string[]; strategies: { name: string; method: string }[] };
  computation: { solver: string; solves: number; solver_seconds: number; n_strategies: number; n_feasible: number; n_distinct: number; all_validated: boolean; status: string };
  options: { key: string; name: string; objective: string; metrics: Record<string, number>; advantages: string[]; disadvantages: string[]; risks: string[] }[];
  recommendation: Recommendation | null;
  limitations: string[];
  narrative: { source: string; text: string; llm_note?: string; template?: string };
  decision_owner: string;
}

export interface UserSelection {
  strategy_id: string;
  strategy_key: string;
  strategy_name: string;
  by: string;
  comment: string;
  follows_recommendation: boolean;
  at: string;
}

export interface LlmUsage {
  calls: number;
  successful: number;
  failed: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  seconds: number;
  models: string[];
}

export interface OperationalHeadline {
  kind: "operational";
  operating_cost_musd: number;
  total_economic_cost_musd: number;
  unserved_gwh: number;
  gas_gwh: number;
  imports_gwh: number;
  emissions_mt: number;
  renewable_share: number;
  min_reserve_margin: number;
  avg_units_in_service: number;
  curtailment_gwh: number;
  avg_cost_usd_per_mwh: number | null;
  demand_gwh: number;
}

export interface OperationalMonth {
  period: string;
  year: number;
  month: number;
  hours: number;
  demand_gwh: number;
  m1_demand_gwh: number;
  demand_source: string;
  peak_mw: number;
  generation_gwh: Record<string, number>;
  imports_gwh: number;
  unserved_gwh: number;
  units_in_service: Record<string, number>;
  units_available: Record<string, number>;
  units_total: Record<string, number>;
  firm_capacity_mw: number;
  reserve_margin_achieved: number;
  variable_cost_musd: number;
  fixed_cost_musd: number;
  unserved_cost_musd: number;
  operating_cost_musd: number;
  gas_gwh: number;
  emissions_mt: number;
  renewable_share: number;
  curtailment_gwh: number;
}

export interface OperationalValidation {
  status: string;
  physical_checks: { check: string; passed: boolean }[];
  constraints: { constraint: string; scope: string; value: number; limit: number; relation: string; unit: string; satisfied: boolean }[];
  objective_consistency: { objective: string; solver_value: number; recomputed: number; passed: boolean }[];
  method: string;
}

export interface ScheduleStep {
  period: string;
  actions: string[];
  units_in_service: Record<string, number>;
  operating_cost_musd: number;
  reserve_margin: number;
  unserved_gwh: number;
  gas_gwh: number;
  imports_gwh: number;
}

export interface StrategyHeadline {
  total_cost_npv_musd: number;
  total_investment_musd: number;
  total_emissions_mt: number;
  final_year_emissions_mt: number;
  emission_change_vs_2024_pct: number;
  renewable_share_final_year: number;
  gas_share_final_year: number;
  dependence_share_horizon: number;
  min_reserve_margin: number;
  kind?: undefined;
}

export interface StrategySummary {
  id: string;
  planning_request_id: string;
  owner_id: number;
  run_id: string | null;
  key: string;
  name: string;
  strategic_objective: string;
  version: number;
  parent_strategy_id: string | null;
  status: string;
  is_reference: boolean;
  feasible: boolean;
  duplicate_of: string | null;
  superseded_by: string | null;
  data_status: DataStatus;
  created_at: string;
  headline: StrategyHeadline | OperationalHeadline | null;
  problem_type?: ProblemType;
  recommended?: boolean;
  selected_by_requester?: boolean;
  request_title?: string;
  requester_email?: string;
}

export interface RoadmapStep {
  year: number;
  actions: string[];
  investment_musd: number;
  new_capacity_mw: TechMap;
  renewable_share: number;
  emissions_mt: number;
  gas_share: number;
  reserve_margin: number;
}

export interface Decision {
  id: number;
  strategy_id: string;
  strategy_version: number;
  strategy_name: string | null;
  admin_id: number;
  admin_email: string;
  decision: string;
  comment: string;
  created_at: string;
}

export interface StrategyDetail extends StrategySummary {
  report: {
    kind?: "operational";
    approach: string;
    headline: StrategyHeadline | OperationalHeadline;
    tradeoff_vs_least_cost: Record<string, number | null | string> | null;
    roadmap: RoadmapStep[];
    schedule?: ScheduleStep[];
    milestones: Record<string, unknown>[];
    advantages: string[];
    disadvantages: string[];
    risks: string[];
    assumptions: string[];
    data_status: DataStatus;
  } | null;
  explanation: { source: string; summary: string; what: string; why: string; when: string; tradeoffs: string[]; llm_note?: string } | null;
  run?: RunSummary & {
    validation: Validation | null;
    solver: SolverInfo | null;
    formulation: Formulation | null;
    provenance: Provenance[] | null;
    objective_config: Record<string, unknown> | null;
    notes: string[] | null;
    annual: AnnualResult[] | null;
    monthly?: OperationalMonth[] | null;
  };
  decisions: Decision[];
  versions: { id: string; version: number; status: string }[];
}
