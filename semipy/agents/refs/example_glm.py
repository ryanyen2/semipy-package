"""
Program-of-Thought code generation: Python from user spec.

Uses pydantic_ai Agent with:
- Pydantic models for tool args/results (output regulation and type checking)
- Reasoning + streaming (thinking and text accumulated per part for readable output)
- Tools: profile_data_and_flow (dependency flow from AST + execution profile), run_python, validate_solution
- Dependency flow extracted from AST in execution order; var state tracked via profiles.
"""

import ast
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai import (
    AgentRunResultEvent,
    AgentStreamEvent,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ThinkingPartDelta,
    ToolCallPartDelta,
)
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings, OpenRouterProvider
from openai import OpenAI

load_dotenv()


# -----------------------------------------------------------------------------
# Pydantic models for tool and run output regulation
# -----------------------------------------------------------------------------


class DataFlowStep(BaseModel):
    """Single step in the data dependency flow (from AST + optional profile). No semantic label—LLM reasons from code_snippet and input_vars."""
    step_id: int
    output_var: str
    input_vars: list[str]
    code_snippet: str
    line_no: Optional[int] = None
    shape_after: Optional[list[int]] = None
    dtypes_summary: Optional[dict[str, str]] = None
    change_summary: str = ""


class ProfileDataAndFlowResult(BaseModel):
    """Result of profile_data_and_flow tool."""
    success: bool
    error: Optional[str] = None
    data_profile: dict[str, Any] = Field(default_factory=dict)
    data_flow: list[DataFlowStep] = Field(default_factory=list)
    summary: str = ""
    insights_placeholder: Optional[str] = None


class EnsurePackagesResult(BaseModel):
    """Result of ensure_packages tool."""
    success: bool
    message: str
    method_used: Optional[str] = None
    installed: list[str] = Field(default_factory=list)


class RunPythonResult(BaseModel):
    """Result of run_python tool."""
    result: Optional[Any] = None
    stdout: str = ""
    error: Optional[str] = None


class ValidateSolutionResult(BaseModel):
    """Result of validate_solution tool."""
    valid: bool
    message: str = ""


class PotDependencies(BaseModel):
    """Dependencies for the Program-of-Thought agent (mutable state)."""
    model_config = {"arbitrary_types_allowed": True}
    spec: str = ""
    final_code: Optional[str] = None
    final_result: Optional[RunPythonResult] = None
    validator_client: Any = None
    use_validator_llm: bool = False
    validated: bool = False
    reasoning_steps: list[dict] = Field(default_factory=list)


def _patch_blocking_calls(code: str) -> str:
    """
    Patch common blocking calls so code can run in a headless/sandbox environment.
    Avoids timeouts from plt.show(), input(), etc. so the LLM gets a result to iterate on.
    """
    # plt.show() blocks; replace with plt.close() so the run completes and we still get answer/result
    if "plt.show()" in code:
        code = code.replace("plt.show()", "plt.close()")
    # input() blocks; replace with a no-op that returns empty string (so code doesn't crash on read)
    if "input(" in code:
        code = code.replace("input(", "(lambda _='': _)(")
    return code


def _run_python_subprocess(
    script_path: str,
    script_args: list[str],
    timeout: int,
    cwd: Optional[str] = None,
) -> tuple[str, str, int]:
    """
    Run a Python script with the current interpreter. Single entry point for all
    user-code execution so behavior is consistent and generalizable.
    Returns (stdout, stderr, returncode). Caller parses stdout as needed.
    """
    cmd = [sys.executable, script_path] + list(script_args)
    run_kwargs = dict(
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if cwd:
        cwd_abs = os.path.abspath(os.path.expanduser(cwd))
        if os.path.isdir(cwd_abs):
            run_kwargs["cwd"] = cwd_abs
    try:
        result = subprocess.run(cmd, **run_kwargs)
        return (result.stdout or "", result.stderr or "", result.returncode)
    except subprocess.TimeoutExpired:
        return ("", "Execution timed out", -1)
    except Exception as e:
        return ("", str(e), -1)


def _exec_code(
    code: str,
    timeout: int = 6,
    cwd: Optional[str] = None,
) -> tuple[Optional[object], str, Optional[str]]:
    """
    Execute user code in a subprocess via _run_python_subprocess.
    Returns (answer_or_None, stdout, error_or_None). Result is read from
    the 'answer' or 'result' variable in the executed namespace.
    Blocking calls (plt.show(), input()) are patched so runs don't time out.
    """
    code = _patch_blocking_calls(code)
    marker = "__PIPS_RESULT__"
    cleanup = []
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            code_path = f.name
            cleanup.append(code_path)
        runner = f"""_locs = {{}}
with open({repr(code_path)}) as _f:
    exec(_f.read(), _locs, _locs)
__out = _locs.get("answer", _locs.get("result"))
print({repr(marker)}, repr(__out), flush=True)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(runner)
            path = f.name
            cleanup.append(path)
        stdout, stderr, returncode = _run_python_subprocess(
            path, [code_path], timeout, cwd=cwd
        )
        err = None
        if returncode != 0:
            err = stderr or f"Process exited with code {returncode}"
        out = None
        if marker in stdout:
            left, _, right = stdout.partition(marker)
            stdout = left.strip()
            try:
                line = right.strip().split("\n")[0].strip()
                if line.startswith("Error:"):
                    err = line
                else:
                    out = eval(line)
            except Exception:
                pass
        return (out, stdout, err)
    except Exception as e:
        return (None, "", str(e))
    finally:
        for p in cleanup:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass


# -----------------------------------------------------------------------------
# Data analysis: AST data-flow + execution profiling
# -----------------------------------------------------------------------------

def _get_names_used(node: ast.AST) -> set:
    """Collect all name ids read from an AST node (for dependency tracking)."""
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Load):
            names.add(n.id)
        if isinstance(n, ast.Attribute):
            if isinstance(n.value, ast.Name):
                names.add(n.value.id)
    return names


def _get_names_assigned(node: ast.AST) -> list[str]:
    """Collect name ids written in an assignment target (Store)."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Store):
            out.append(n.id)
    return out


def _get_code_snippet(source: str, node: ast.AST) -> str:
    """Return the source slice for a node if we have full source."""
    try:
        return ast.get_source_segment(source, node) or ""
    except Exception:
        return ""


def _collect_assignments_in_order(
    body: list[ast.AST], source: str, steps: list, step_id: list
) -> None:
    """
    Walk statement list in execution order; append assignment steps.
    Recurses into compound statements (For, While, With, etc.) to preserve order.
    """
    for node in body:
        if isinstance(node, ast.Assign):
            value = node.value
            input_vars = _get_names_used(value)
            for target in node.targets:
                out_names = _get_names_assigned(target)
                if not out_names:
                    continue
                out_var = out_names[0]
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
                    if isinstance(value.func.value, ast.Name):
                        input_vars.add(value.func.value.id)
                snippet = _get_code_snippet(source, node)
                steps.append({
                    "step_id": step_id[0],
                    "output_var": out_var,
                    "input_vars": list(input_vars),
                    "code_snippet": snippet.strip() or f"{out_var} = ...",
                    "line_no": getattr(node, "lineno", None),
                })
                step_id[0] += 1
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            out_names = _get_names_assigned(node.target)
            if out_names:
                out_var = out_names[0]
                input_vars = _get_names_used(node.value)
                snippet = _get_code_snippet(source, node)
                steps.append({
                    "step_id": step_id[0],
                    "output_var": out_var,
                    "input_vars": list(input_vars),
                    "code_snippet": snippet.strip() or f"{out_var} = ...",
                    "line_no": getattr(node, "lineno", None),
                })
                step_id[0] += 1
        elif isinstance(node, ast.AugAssign):
            out_names = _get_names_assigned(node.target)
            if out_names:
                out_var = out_names[0]
                input_vars = _get_names_used(node.target) | _get_names_used(node.value)
                snippet = _get_code_snippet(source, node)
                steps.append({
                    "step_id": step_id[0],
                    "output_var": out_var,
                    "input_vars": list(input_vars),
                    "code_snippet": snippet.strip() or f"{out_var} ...= ...",
                    "line_no": getattr(node, "lineno", None),
                })
                step_id[0] += 1
        elif isinstance(node, (ast.For, ast.While)):
            _collect_assignments_in_order(node.body, source, steps, step_id)
            if getattr(node, "orelse", None):
                _collect_assignments_in_order(node.orelse, source, steps, step_id)
        elif isinstance(node, ast.With):
            for with_item in node.items:
                if with_item.optional_vars is not None:
                    out_names = _get_names_assigned(with_item.optional_vars)
                    if out_names:
                        input_vars = _get_names_used(with_item.context_expr)
                        snippet = _get_code_snippet(source, node)
                        steps.append({
                            "step_id": step_id[0],
                            "output_var": out_names[0],
                            "input_vars": list(input_vars),
                            "code_snippet": snippet.strip() or f"with ... as {out_names[0]}",
                            "line_no": getattr(node, "lineno", None),
                        })
                        step_id[0] += 1
            _collect_assignments_in_order(node.body, source, steps, step_id)
        elif isinstance(node, ast.If):
            _collect_assignments_in_order(node.body, source, steps, step_id)
            if node.orelse:
                _collect_assignments_in_order(node.orelse, source, steps, step_id)
        elif isinstance(node, ast.Try):
            _collect_assignments_in_order(node.body, source, steps, step_id)
            for handler in node.handlers:
                _collect_assignments_in_order(handler.body, source, steps, step_id)
            if node.orelse:
                _collect_assignments_in_order(node.orelse, source, steps, step_id)
            if node.finalbody:
                _collect_assignments_in_order(node.finalbody, source, steps, step_id)


def _extract_data_flow_ast(code: str) -> list:
    """
    Extract dependency flow from AST in execution order. Each step records
    output_var (main assigned name), input_vars (names read), code_snippet.
    No semantic labels—LLM classifies and reasons from usage and code.
    State changes can be tracked by merging with execution profiles per step.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    steps = []
    step_id = [0]
    _collect_assignments_in_order(tree.body, code, steps, step_id)
    return steps


def _run_and_profile_dataframes(
    code: str, timeout: int = 15, working_dir: Optional[str] = None
) -> tuple:
    """
    Execute code in subprocess and profile every pandas DataFrame/Series in the global scope.
    working_dir: if set, run the code with this cwd so relative paths (e.g. to local CSV) resolve.
    Returns (profiles_dict, stderr). profiles_dict: var_name -> {shape, dtypes, head, describe, nulls, ...}.
    """
    runner = """
import json
import sys
_CODE_PATH = sys.argv[1]
_locs = {}
try:
    with open(_CODE_PATH) as _f:
        exec(_f.read(), _locs, _locs)
except Exception as e:
    print("__PROFILE_ERROR__", str(e), file=sys.stderr)
    sys.exit(1)
def _profile(obj):
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            return {
                "type": "DataFrame",
                "shape": list(obj.shape),
                "columns": list(obj.columns),
                "dtypes": {k: str(v) for k, v in obj.dtypes.items()},
                "head": obj.head(5).fillna("__NA__").to_dict(orient="records"),
                "describe": obj.describe(include="all").fillna("__NA__").to_dict() if len(obj) > 0 else {},
                "null_counts": obj.isnull().sum().to_dict(),
                "memory_mb": round(obj.memory_usage(deep=True).sum() / 1024 / 1024, 2),
            }
        if isinstance(obj, pd.Series):
            return {
                "type": "Series",
                "shape": [len(obj)],
                "dtype": str(obj.dtype),
                "head": obj.head(10).fillna("__NA__").tolist(),
                "null_count": int(obj.isnull().sum()),
            }
    except Exception:
        pass
    return None
_out = {}
for _k, _v in _locs.items():
    if _k.startswith("_"):
        continue
    _p = _profile(_v)
    if _p is not None:
        _out[_k] = _p
print("__PIPS_PROFILE__")
print(json.dumps(_out, default=str))
"""
    cleanup = []
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            code_path = f.name
            cleanup.append(code_path)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(runner)
            runner_path = f.name
            cleanup.append(runner_path)
        stdout, stderr, returncode = _run_python_subprocess(
            runner_path, [code_path], timeout, cwd=working_dir
        )
        if returncode != 0:
            return {}, stderr or f"Exit code {returncode}"
        if "__PIPS_PROFILE__" not in stdout:
            return {}, stderr or "No profile output"
        try:
            json_str = stdout.split("__PIPS_PROFILE__", 1)[1].strip()
            profiles = json.loads(json_str)
            return profiles, stderr
        except json.JSONDecodeError as e:
            return {}, stderr or str(e)
    except subprocess.TimeoutExpired:
        return {}, "Execution timed out"
    except Exception as e:
        return {}, str(e)
    finally:
        for p in cleanup:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass


def _build_data_flow_with_profiles(ast_steps: list, profiles: dict) -> list:
    """Merge AST steps with execution profiles; add shape_after and change_summary."""
    flow = []
    prev_shape = None
    prev_columns = None
    for s in ast_steps:
        out_var = s["output_var"]
        prof = profiles.get(out_var)
        shape_after = None
        dtypes_summary = None
        change_summary = ""
        if prof:
            shape_after = prof.get("shape")
            cols = prof.get("columns")
            dtypes_summary = prof.get("dtypes")
            if shape_after is not None:
                change_summary = f"shape {shape_after[0]} rows × {shape_after[1]} cols"
                if prev_shape is not None and prev_shape != shape_after:
                    dr = (shape_after[0] - prev_shape[0]) if len(prev_shape) > 0 else 0
                    dc = (shape_after[1] - prev_shape[1]) if len(prev_shape) > 1 else 0
                    parts = []
                    if dr != 0:
                        parts.append(f"rows {prev_shape[0]} → {shape_after[0]}")
                    if dc != 0 and prev_columns is not None and cols is not None:
                        added = set(cols) - set(prev_columns)
                        removed = set(prev_columns) - set(cols)
                        if added:
                            parts.append(f"added cols: {list(added)}")
                        if removed:
                            parts.append(f"removed cols: {list(removed)}")
                    if parts:
                        change_summary = "; ".join(parts)
            if prev_shape is not None:
                prev_shape = shape_after
                prev_columns = cols
            else:
                prev_shape = shape_after
                prev_columns = cols
        flow.append({
            **s,
            "shape_after": shape_after,
            "dtypes_summary": dtypes_summary,
            "change_summary": change_summary or "(no change info)",
        })
    return flow


def profile_data_and_flow_impl(
    code: str, timeout: int = 15, working_dir: Optional[str] = None
) -> dict:
    """
    Deterministic: AST data flow + execution profiling.
    working_dir: run code with this cwd so relative paths (e.g. pd.read_csv('data/x.csv')) resolve.
    Returns: data_profile (concise per-var), data_flow (steps with shape/summary), summary (text for LLM).
    """
    ast_steps = _extract_data_flow_ast(code)
    profiles, exec_err = _run_and_profile_dataframes(
        code, timeout=timeout, working_dir=working_dir
    )
    if exec_err and not profiles:
        return {
            "success": False,
            "error": exec_err,
            "data_profile": {},
            "data_flow": [],
            "summary": f"Execution failed: {exec_err}. No data profile available.",
        }
    flow = _build_data_flow_with_profiles(ast_steps, profiles)
    # Concise data profile (main vars only, key stats)
    data_profile = {}
    for var, p in profiles.items():
        if p.get("type") == "DataFrame":
            data_profile[var] = {
                "type": "DataFrame",
                "shape": p.get("shape"),
                "columns": p.get("columns"),
                "dtypes": p.get("dtypes"),
                "null_counts": p.get("null_counts"),
                "memory_mb": p.get("memory_mb"),
                "head_sample": p.get("head"),
            }
        else:
            data_profile[var] = p
    summary_parts = [
        "Data profile: " + ", ".join(f"{k} {v.get('type', '?')} shape={v.get('shape')}" for k, v in data_profile.items()),
        "Data flow: " + str(len(flow)) + " steps.",
    ]
    for step in flow:
        summary_parts.append(
            f"  Step {step['step_id']}: {step['output_var']} <- {step['input_vars']} | {step.get('change_summary', '')}"
        )
    return {
        "success": True,
        "data_profile": data_profile,
        "data_flow": flow,
        "summary": "\n".join(summary_parts),
        "insights_placeholder": "Add your insights per step and overall recommendations (e.g. for outlier removal) based on the profile and flow above.",
    }


# -----------------------------------------------------------------------------
# Agent and tools (pydantic_ai with OpenRouter)
# -----------------------------------------------------------------------------

VALIDATOR_MODEL = "z-ai/glm-4.7-flash"
CHAT_MODEL = "z-ai/glm-5"

_openrouter_model = OpenRouterModel(
    CHAT_MODEL,
    provider=OpenRouterProvider(api_key=os.getenv("OPENROUTER_API_KEY")),
)
_model_settings = OpenRouterModelSettings(
    openrouter_reasoning={"effort": "high"},
    temperature=0.0,
)

SYSTEM_PROMPT = """You are a code-generation assistant. Given a user specification, you must:

0. **If the user provides data analysis code** (e.g. pd.read_csv, preprocessing, a script that loads/processes data): call profile_data_and_flow(code, working_dir?) FIRST. This runs their code to get the real data profile and a data flow (dependency flow from AST in execution order). Use working_dir when their code uses relative paths to local files (e.g. "data/train.csv")—set it to the directory that contains those files. If profile_data_and_flow fails with ModuleNotFoundError or missing package (e.g. sklearn), call ensure_packages with the required PyPI names (e.g. ["scikit-learn"]), then retry profile_data_and_flow.

1. Reason step-by-step (plan, then write code).
2. Use run_python to execute your code and see the result.
3. Iterate if the result is wrong or there are errors.
4. When the solution satisfies the spec, call validate_solution with the final code and result summary and satisfies_spec=True.

You have four tools (use in this order when applicable):
- profile_data_and_flow(code, working_dir?): run FIRST when user gives data analysis code. Optional working_dir = directory to run from so relative paths resolve. Returns data_profile, data_flow (dependency steps with shape/change summary), summary.
- ensure_packages(packages): install packages (e.g. ["scikit-learn"]) so profile/run_python can use them. Call when you get ModuleNotFoundError, then retry the failed tool.
- run_python(code): runs Python code; use variable 'answer' or 'result' for the return value.
- validate_solution(code, result_summary, satisfies_spec): call when done to confirm the solution meets the spec.

Always reason first (plan), then use tools. No file or network access in code except as in the user's own data-loading code."""

pot_agent = Agent(
    _openrouter_model,
    model_settings=_model_settings,
    deps_type=PotDependencies,
    system_prompt=SYSTEM_PROMPT,
)


@pot_agent.tool
async def profile_data_and_flow(
    ctx: RunContext[PotDependencies],
    code: str,
    working_dir: Optional[str] = None,
) -> ProfileDataAndFlowResult:
    """Run FIRST when the user provides data analysis code. Executes the code to get real data profiles and builds a dependency flow from AST (execution order). Returns data_profile, data_flow (steps with shape_after and change_summary), summary. If the code uses relative paths, set working_dir to the directory containing those files."""
    raw = profile_data_and_flow_impl(code, timeout=15, working_dir=working_dir)
    flow = [DataFlowStep(**s) for s in raw.get("data_flow", [])]
    return ProfileDataAndFlowResult(
        success=raw.get("success", False),
        error=raw.get("error"),
        data_profile=raw.get("data_profile", {}),
        data_flow=flow,
        summary=raw.get("summary", ""),
        insights_placeholder=raw.get("insights_placeholder"),
    )


@pot_agent.tool
async def ensure_packages(
    ctx: RunContext[PotDependencies],
    packages: list[str],
) -> EnsurePackagesResult:
    """Install Python packages so that profile_data_and_flow and run_python can use them. Call when you get ModuleNotFoundError (e.g. sklearn), then retry the failed tool. Use PyPI names (scikit-learn not sklearn)."""
    out = ensure_packages_impl(packages)
    return EnsurePackagesResult(
        success=out["success"],
        message=out["message"],
        method_used=out.get("method_used"),
        installed=out.get("installed", []),
    )


@pot_agent.tool
async def run_python(
    ctx: RunContext[PotDependencies],
    code: str,
) -> RunPythonResult:
    """Execute the given Python code in a sandbox. Use a variable 'answer' or 'result' to hold the final value to return. Timeout 5s."""
    out = run_python_impl(code)
    ctx.deps.final_code = code
    ctx.deps.final_result = RunPythonResult(
        result=out.get("result"),
        stdout=out.get("stdout", ""),
        error=out.get("error"),
    )
    return ctx.deps.final_result


@pot_agent.tool
async def validate_solution(
    ctx: RunContext[PotDependencies],
    code: str,
    result_summary: str,
    satisfies_spec: bool,
) -> ValidateSolutionResult:
    """Call when you believe the code and its output meet the spec. Pass the final code, a short result summary, and satisfies_spec=True if it satisfies."""
    if not satisfies_spec:
        return ValidateSolutionResult(valid=False, message="Model reported solution does not satisfy spec.")
    if ctx.deps.use_validator_llm and ctx.deps.validator_client:
        out = validate_impl(
            ctx.deps.spec,
            code,
            result_summary,
            satisfies_spec,
            ctx.deps.validator_client,
        )
        res = ValidateSolutionResult(valid=out["valid"], message=out.get("message", ""))
    else:
        res = ValidateSolutionResult(valid=satisfies_spec, message="Model self-check.")
    if res.valid:
        ctx.deps.validated = True
    return res


def _probe_installer() -> Optional[tuple[list[str], str]]:
    """
    Discover which package installer is available for the current interpreter.
    Returns (cmd_list, method_name) or None. No hardcoded order: we probe each
    mechanism and use the first that succeeds. Generalizable to conda, uv, pip,
    ensurepip, etc.
    """
    # Probe: can we run this module as an installer? (same execution model as user code)
    probes = [
        ([sys.executable, "-m", "uv", "pip", "install", "--version"], "uv"),
        ([sys.executable, "-m", "pip", "--version"], "pip"),
    ]
    for cmd, name in probes:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                install_cmd = (
                    [sys.executable, "-m", "uv", "pip", "install", "--quiet"]
                    if name == "uv"
                    else [sys.executable, "-m", "pip", "install", "--quiet"]
                )
                return (install_cmd, name)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    # Bootstrap: ensurepip is in the standard library
    try:
        r = subprocess.run(
            [sys.executable, "-m", "ensurepip", "--default-pip"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode == 0:
            return ([sys.executable, "-m", "pip", "install", "--quiet"], "pip (via ensurepip)")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def ensure_packages_impl(packages: list[str]) -> dict:
    """
    Install packages using whatever installer is available for the current
    interpreter (discovered by probing, not keyword matching). Returns
    {success, message, method_used, installed}.
    """
    if not packages:
        return {"success": True, "message": "No packages requested.", "method_used": None, "installed": []}
    installer = _probe_installer()
    if not installer:
        return {
            "success": False,
            "message": "No installer found. Try: python -m ensurepip or install pip/uv for this interpreter.",
            "method_used": None,
            "installed": [],
        }
    cmd_list, method_name = installer
    cmd = cmd_list + list(packages)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return {
                "success": True,
                "message": f"Installed with {method_name}: {packages}",
                "method_used": method_name,
                "installed": packages,
            }
        return {
            "success": False,
            "message": (r.stderr or r.stdout or f"Exit code {r.returncode}"),
            "method_used": method_name,
            "installed": [],
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Install timed out.", "method_used": method_name, "installed": []}
    except Exception as e:
        return {"success": False, "message": str(e), "method_used": method_name, "installed": []}


def run_python_impl(code: str, timeout: int = 5) -> dict:
    """Execute code; return dict with result, stdout, error for tool response. Patches blocking calls to avoid timeout."""
    out, stdout, err = _exec_code(code, timeout=timeout)
    # When timeout still happens, return a hint so the LLM can fix (e.g. use savefig instead of show)
    if err and "timed out" in err.lower():
        err = (
            "Execution timed out, please retry."
        )
    return {
        "result": out,
        "stdout": stdout,
        "error": err,
    }


def validate_impl(
    spec: str, code: str, result_summary: str, satisfies_spec: bool, client: Any
) -> dict:
    """
    Validator: optionally use LLM to double-check. Be tolerant—accept if solution
    substantially meets the user request. Always return a non-empty message when valid=False.
    """
    if not satisfies_spec:
        return {
            "valid": False,
            "message": "Model reported solution does not satisfy spec.",
        }

    try:
        resp = client.chat.completions.create(
            model=VALIDATOR_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a intent-code matching validator. Only say NO if the solution clearly fails to meet the user's specification. "
                        "If the solution substantially satisfies the user's request (even if not perfect), say YES. It is okay to say YES if the solution is not perfect, as long as it is close to the user's request. "
                        "Reply with YES or NO first, then one short sentence. If NO, you MUST give the reason (e.g. what is missing or wrong)."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Spec: {spec}\n\nCode (summary of what it does): {result_summary}\n\nDoes this satisfy the spec? Reply YES or NO, then one sentence.",
                },
            ],
            max_tokens=120,
        )
        raw = (resp.choices[0].message.content or "").strip()
        text = raw.upper()
        # Tolerant: accept if YES appears, or no clear NO at start, or empty response (trust model)
        if not text:
            valid, message = True, "Validator gave no response; trusting model's satisfaction."
        else:
            valid = "YES" in text or "NO" not in text[:50]
            message = raw
            if not valid and not message:
                message = "Validator did not confirm (no reason given). If the solution meets the user request, it may still be accepted."
        return {"valid": valid, "message": message}
    except Exception as e:
        # Fallback: trust model's satisfies_spec
        return {"valid": bool(satisfies_spec), "message": f"Validator fallback: {e}"}


# -----------------------------------------------------------------------------
# Program-of-Thought run: streaming with accumulated output
# -----------------------------------------------------------------------------

# Accumulate streaming deltas per part and print full blocks (not line-by-line).
_part_buffers: dict[int, str] = {}
_current_part_index: int | None = None
_current_part_type: str | None = None
_reasoning_blocks: list[str] = []


def _flush_part():
    global _current_part_index, _current_part_type
    if _current_part_index is not None and _part_buffers.get(_current_part_index):
        content = _part_buffers[_current_part_index].strip()
        if content:
            if _current_part_type == "thinking":
                print("\n--- Thinking ---\n" + content + "\n")
                _reasoning_blocks.append(content)
            else:
                print("\n--- Response ---\n" + content + "\n")
    _part_buffers.clear()
    _current_part_index = None
    _current_part_type = None


async def _handle_stream_event(event: AgentStreamEvent | AgentRunResultEvent):
    global _current_part_index, _current_part_type
    if isinstance(event, PartStartEvent):
        _flush_part()
        _current_part_index = event.index
        part_type_name = type(event.part).__name__
        _current_part_type = "thinking" if "Thinking" in part_type_name else "text"
    elif isinstance(event, PartDeltaEvent):
        delta = event.delta
        content = ""
        if isinstance(delta, ThinkingPartDelta):
            content = delta.content_delta or ""
            _current_part_type = "thinking"
        elif isinstance(delta, TextPartDelta):
            content = delta.content_delta or ""
            _current_part_type = "text"
        if content:
            _part_buffers.setdefault(event.index, "")
            _part_buffers[event.index] += content
    elif isinstance(event, FinalResultEvent):
        _flush_part()
    elif isinstance(event, FunctionToolCallEvent):
        print(f"[Tools] {event.part.tool_name!r} called with args: {event.part.args}")
    elif isinstance(event, FunctionToolResultEvent):
        result = event.result
        content_preview = str(getattr(result, "content", ""))[:200]
        if len(str(getattr(result, "content", ""))) > 200:
            content_preview += "..."
        tool_name = getattr(result, "tool_name", "?")
        print(f"[Tools] {tool_name!r} returned => {content_preview}")
    if isinstance(event, AgentRunResultEvent):
        _flush_part()
        print(f"[Final output] {event.result.output}")


def _reset_stream_state():
    global _part_buffers, _current_part_index, _current_part_type, _reasoning_blocks
    _part_buffers = {}
    _current_part_index = None
    _current_part_type = None
    _reasoning_blocks = []


async def run_program_of_thought(
    user_spec: str,
    max_rounds: int = 5,
    use_validator_llm: bool = False,
):
    """
    Code generation from user spec using pydantic_ai Agent with reasoning and tools.
    Streams response with accumulated thinking/response blocks (not line-by-line deltas).
    """
    _reset_stream_state()
    validator_client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    deps = PotDependencies(
        spec=user_spec,
        validator_client=validator_client,
        use_validator_llm=use_validator_llm,
    )
    user_message = f"User specification:\n{user_spec}"

    async for event in pot_agent.run_stream_events(user_message, deps=deps):
        await _handle_stream_event(event)

    reasoning_steps = [{"round": i + 1, "reasoning": r} for i, r in enumerate(_reasoning_blocks)]
    final_result = deps.final_result
    return {
        "reasoning_steps": reasoning_steps,
        "final_code": deps.final_code,
        "final_result": final_result.model_dump() if isinstance(final_result, RunPythonResult) else final_result,
        "valid": deps.validated,
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
# Local data: If the user's code loads files with relative paths (e.g. pd.read_csv("data/train.csv")),
# the agent can call profile_data_and_flow(code, working_dir="/path/to/project"). Run this script from
# the project root, or pass the project path in the user spec so the agent knows the working_dir.
# Packages: The profiling subprocess uses the same Python as this script (e.g. "uv run python tests/glm.py").
# If a package is missing (e.g. sklearn), the agent can call ensure_packages(["scikit-learn"]) then retry.
# You can also add optional deps to pyproject.toml so the venv has them by default.

if __name__ == "__main__":
    spec = (""" User current program:
```python
import pandas as pd
df = pd.read_csv("/Users/r4yen/Desktop/Research/semi-formal/repo/pips/tests/data/seattle-weather.csv")

df = df.dropna()
plot_seasonality(df, "precipitation")
```

and now please implement the function plot_seasonality(df, column) that plots the seasonality of the given column.
Rule: the plot should show the trend over the year.
"""
    )
    if len(sys.argv) > 1:
        spec = " ".join(sys.argv[1:])
    result = asyncio.run(run_program_of_thought(spec, max_rounds=8, use_validator_llm=True))
    print("\n=== Reasoning steps (core output) ===")
    for step in result["reasoning_steps"]:
        print(f"[Round {step['round']}]\n{step['reasoning']}\n")
    print("=== Final code ===")
    print(result.get("final_code") or "(none)")
    print("=== Final result ===")
    print(result.get("final_result"))
    print("=== Valid ===")
    print(result.get("valid"))
