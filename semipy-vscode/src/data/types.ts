export interface CommitJson {
  commit_id: string;
  parent_ids: string[];
  generated_source: string;
  timestamp: number;
  message: string;
  decision: string;
  binding_id?: string;
}

export interface BranchJson {
  name: string;
  head: string;
}

export interface SlotSpecJson {
  slot_id?: string;
  spec_text?: string;
  source_span?: [string, number, number];
  expected_category?: string;
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

export interface SketchLibraryJson {
  bindings?: Record<string, SemanticBindingJson>;
  sketches?: Record<string, { binding_id?: string; sketch_id?: string }>;
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
