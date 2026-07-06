export interface CommitSourceSnapshotJson {
  slot_region_text?: string;
  slot_region_start_line?: number;
  slot_region_end_line?: number;
  source_file?: string;
}

/** One before -> after entry recorded by effect-diff on GENERATE/ADAPT. */
export interface ChangeDiffEntryJson {
  input_fingerprint?: string;
  input_repr?: string;
  old_repr?: string;
  new_repr?: string;
  /** true when on the triggering input (or the parent was already wrong here). */
  intended?: boolean;
}

/** Why a regeneration happened and what its effect was (Commit.change_record). */
export interface ChangeRecordJson {
  reason?: string;
  triggering_input_fingerprint?: string;
  decision?: string;
  parent_commit_id?: string;
  unintended_count?: number;
  n_compared?: number;
  effect_diff?: ChangeDiffEntryJson[];
}

/** One steering value, with whether the user has frozen it (promoted to #>). */
export interface SteeringValueJson {
  value?: string;
  input_sig?: string;
  user_frozen?: boolean;
}

/** Structured #< surface: provenance (intent/given/by/unless) + effect (yields/verified).
 *  Mirrors semipy.models.SteeringBlock. */
export interface SteeringJson {
  intent?: SteeringValueJson;
  given?: SteeringValueJson | SteeringValueJson[];
  by?: SteeringValueJson;
  unless?: SteeringValueJson | SteeringValueJson[];
  yields?: SteeringValueJson;
  verified?: SteeringValueJson;
  [key: string]: SteeringValueJson | SteeringValueJson[] | undefined;
}

export interface CommitmentRecordJson {
  steering?: SteeringJson;
  [key: string]: unknown;
}

export interface CommitJson {
  commit_id: string;
  parent_ids: string[];
  generated_source: string;
  timestamp: number;
  message: string;
  decision: string;
  binding_id?: string;
  source_snapshot?: CommitSourceSnapshotJson;
  change_record?: ChangeRecordJson;
  commitment_record?: CommitmentRecordJson;
  runtime_input_fingerprint?: string;
}

// --- Behavioral contract (why/effect of changes) --------------------------

export type ContractCaseKind = "example" | "invariant" | "metamorphic";
export type ContractCaseStatus = "active" | "superseded" | "quarantined";

export interface ContractCaseJson {
  case_id: string;
  kind: ContractCaseKind;
  input_sample?: Record<string, unknown>;
  input_fingerprint?: string;
  expected_repr?: string;
  expected_type?: string;
  invariant?: string;
  relation?: string;
  relation_param?: Record<string, unknown>;
  reason?: string;
  effect?: string;
  decision?: string;
  origin_commit_id?: string;
  created_ts?: number;
  updated_ts?: number;
  status?: ContractCaseStatus;
  superseded_by?: string;
  supersede_reason?: string;
}

export interface SlotContractJson {
  version?: number;
  cases?: Record<string, ContractCaseJson>;
}

// --- Effects (reified real-world effects) ---------------------------------

export type EffectOp = "create" | "read" | "update" | "delete" | "append" | "call";
export type LedgerEventStatus = "applied" | "reverted" | "shadow" | "approval_pending";

export interface EffectJson {
  op: EffectOp;
  target: string;
  payload?: Record<string, unknown>;
  selector?: Record<string, unknown> | null;
  compensation?: EffectJson | null;
  provenance?: Record<string, unknown>;
  effect_id?: string;
}

export interface LedgerEventJson {
  event_id: string;
  slot_id?: string;
  origin_commit_id?: string;
  invocation_id?: string;
  applied_effects?: EffectJson[];
  compensations?: EffectJson[];
  artifact_snapshot_ref?: string;
  contract_case_ids?: string[];
  status?: LedgerEventStatus;
  timestamp?: number;
  parent_event_id?: string;
}

export interface EffectLedgerJson {
  version?: number;
  events?: LedgerEventJson[];
}

// --- Decisions (surfaced silent forks) ------------------------------------
// Mirrors semipy.decisions.model (Branch / Decision / DecisionSet). The render
// contract for the #? fork UI: opacity = weight, axis_label/fate_label are
// user-language names (empty -> show the deterministic output-cluster view).

/** One behavioral fate a set of candidates gave the ambiguity germ. */
export interface DecisionBranchJson {
  fate_label: string;
  candidate_ids: string[];
  weight: number;
  signature?: string[];
  example_in?: unknown;
  example_out?: unknown;
}

/** One surfaced fork: a germ, its fates, an optional guard, a distribution. */
export interface DecisionJson {
  decision_id: string;
  germ: string;
  axis_label: string;
  branches: DecisionBranchJson[];
  guard?: string | null;
  consequence?: number;
  consequence_kind?: string;
  /** "open" | "resolved". */
  status: string;
  /** {via:"pick",branch,...} | {via:"assert",property,contract_case_id,...} | null. */
  resolution?: Record<string, unknown> | null;
  /** true when an LLM named axis/fates; false = deterministic output-cluster view. */
  labeled?: boolean;
}

/** All decisions for one slot resolution + every candidate source (incl. losers). */
export interface DecisionSetJson {
  slot_id?: string;
  decisions: DecisionJson[];
  candidates: Record<string, string>;
}

export interface BranchJson {
  name: string;
  head: string;
}

/** kernel.operators.FreezeCertificate: the recorded license (or refusal) for one freeze attempt. */
export interface FreezeCertificateJson {
  epsilon: number;
  delta: number;
  gamma: number;
  budget_total: number;
  budget_spent: number;
  held_out_pass_fraction: number;
  mdl_gain: number;
  licensed: boolean;
  refusal_reasons: string[];
}

/** kernel.operators.FreezeEvent: one freeze attempt, licensed or refused. */
export interface FreezeEventJson {
  certificate: FreezeCertificateJson;
  node_id: string;
  source_len: number;
  timestamp: number;
}

export interface SlotSpecJson {
  slot_id?: string;
  spec_text?: string;
  source_span?: [string, number, number];
  /** [file, start_line, end_line] 1-based; function containing the slot */
  enclosing_function_span?: [string, number, number];
  enclosing_function_qualname?: string;
  expected_category?: string;
  expected_type?: string;
  free_variables?: string[];
  output_names?: string[] | null;
  spec_equivalence_key?: string;
  [key: string]: unknown;
}

export interface SlotJson {
  slot_id: string;
  call_site_info?: Record<string, unknown>;
  function_name_base: string;
  commits: Record<string, CommitJson>;
  branches: Record<string, BranchJson>;
  refs: Record<string, string>;
  default_branch: string;
  slot_spec?: SlotSpecJson | null;
  /** Behavioral contract (why/effect of changes); {} on portals predating contracts. */
  contract?: SlotContractJson;
  /** Append-only effect ledger; {} on portals predating effects. */
  ledger?: EffectLedgerJson;
  /** Surfaced silent decisions + candidate sources; {} on unambiguous/legacy slots. */
  decision_set?: DecisionSetJson;
  advisor_state?: Record<string, unknown>;
  input_observation_samples?: Record<string, string[]>;
  /** Every freeze attempt (licensed or refused); [] on portals predating certified freezing. */
  freeze_events?: FreezeEventJson[];
}

export interface PortalJson {
  session_id: string;
  source_file: string;
  module_name: string;
  slots: Record<string, SlotJson>;
  spec_map: Record<string, string>;
  enclosing_function_slots: Record<string, string[]>;
}

export interface SpecPhraseJson {
  text: string;
  role: string;
  code_referent: string;
  hole_name?: string | null;
  safe_swap_set?: string[] | null;
}

export interface SemanticBindingJson {
  binding_id: string;
  spec_text: string;
  phrases: SpecPhraseJson[];
}

export interface SketchRecordJson {
  binding_id?: string;
  sketch_id?: string;
  /** Commits that produced this sketch; used to link portal commits to bindings when `commit.binding_id` was never written. */
  source_commit_ids?: string[];
}

export interface SketchLibraryJson {
  version?: number;
  bindings?: Record<string, SemanticBindingJson>;
  sketches?: Record<string, SketchRecordJson>;
}

export interface DiagnosticEntryJson {
  slot_id: string;
  source_file: string;
  source_line_start: number;
  source_line_end: number;
  severity: string;
  message: string;
  generated_path: string;
  generated_line_range: [number, number];
  code: string;
}

export interface DiagnosticsFileJson {
  entries: DiagnosticEntryJson[];
}
