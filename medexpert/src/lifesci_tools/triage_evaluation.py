"""Triage Evaluation Tool — cross-references diagnosis against symptoms.

Uses an LLM to assess how well the consensus diagnosis matches the
symptoms in the clinical notes.  Flags for human review when discrepancies
are detected.

Emergency path: when ``emergency_override=true``, skips consensus
cross-referencing and validates that clinical note symptoms are consistent
with an emergency classification.
"""

import json
import logging
from pathlib import Path

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.triage_utils import emit_triage_progress, extract_json_from_text

log = logging.getLogger(__name__)


def _load_eval_prompt(prompts_dir: str = "") -> str:
    """Load eval_system.md."""
    if not prompts_dir:
        prompts_dir = str(
            Path(__file__).resolve().parents[2] / "prompts" / "triage"
        )
    path = Path(prompts_dir) / "eval_system.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


class TriageEvaluationTool(DynamicTool):
    """LLM-based evaluation of consensus diagnosis against clinical notes."""

    @property
    def tool_name(self) -> str:
        return "triage_evaluation"

    @property
    def tool_description(self) -> str:
        return (
            "Evaluates the consensus diagnosis by cross-referencing it against "
            "the patient's symptoms. Uses an LLM to assess match quality and "
            "flags for human review when discrepancies exist. "
            "In emergency mode, validates emergency classification against symptoms."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "consensus_diagnosis": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The consensus diagnosis from the panel",
                ),
                "mean_confidence": adk_types.Schema(
                    type=adk_types.Type.NUMBER,
                    description="Mean confidence from consensus (0-100)",
                ),
                "clinical_note": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON string of the structured clinical note",
                ),
                "supporting_specialists": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON array of supporting specialist names",
                    nullable=True,
                ),
                "dissenting_specialists": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON array of dissenting specialist objects",
                    nullable=True,
                ),
                "emergency_override": adk_types.Schema(
                    type=adk_types.Type.BOOLEAN,
                    description="Whether emergency was detected during intake",
                    nullable=True,
                ),
            },
            required=["consensus_diagnosis", "mean_confidence", "clinical_note"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict:
        diagnosis = args.get("consensus_diagnosis", "")
        mean_conf = float(args.get("mean_confidence", 0))
        clinical_note = args.get("clinical_note", "{}")
        emergency = bool(args.get("emergency_override", False))

        # Parse dissenting specialists
        raw_dissent = args.get("dissenting_specialists", "[]")
        try:
            dissenting = json.loads(raw_dissent) if isinstance(raw_dissent, str) else (raw_dissent or [])
        except (json.JSONDecodeError, TypeError):
            dissenting = []

        model = (self.tool_config or {}).get("model", "openai/gemini-2.5-flash-001")
        temperature = float((self.tool_config or {}).get("temperature", 0.2))

        # Emergency fast-path — validate emergency classification
        if emergency:
            result = _evaluate_emergency(diagnosis, clinical_note)
            # Emit stage 4 progress (cumulative — includes prior stages)
            await emit_triage_progress(
                tool_context,
                stage=4,
                stage_name="EVALUATION",
                detail="Emergency classification validated",
                evaluation=result,
                emergency_override=True,
                log_identifier="[TriageEval:Progress]",
            )
            return result

        # INCONCLUSIVE — skip LLM, flag for review
        if diagnosis == "INCONCLUSIVE":
            return {
                "diagnosis": diagnosis,
                "eval_confidence": 0,
                "explanation": "Specialist panel could not reach consensus.",
                "flag_for_review": True,
                "flag_reason": "No consensus diagnosis was reached.",
                "confirmed_emergency": False,
            }

        # Normal path — LLM cross-reference
        eval_prompt = _load_eval_prompt(
            (self.tool_config or {}).get("prompts_dir", "")
        )

        user_content = json.dumps({
            "consensus_diagnosis": diagnosis,
            "mean_confidence": mean_conf,
            "supporting_specialists": args.get("supporting_specialists", "[]"),
            "dissenting_specialists": raw_dissent,
            "structured_clinical_note": clinical_note,
        })

        try:
            import litellm

            response = litellm.completion(
                model=model,
                messages=[
                    {"role": "system", "content": eval_prompt or _DEFAULT_EVAL_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=temperature,
                max_tokens=500,
            )
            text = response.choices[0].message.content.strip()

            result = extract_json_from_text(text)
            if result is None:
                raise json.JSONDecodeError(
                    "Evaluation response returned None after extraction",
                    "[response redacted]",
                    0,
                )
            eval_conf = int(result.get("eval_confidence", 50))
            flag = bool(result.get("flag_for_review", False))
            flag_reason = result.get("flag_reason")

        except Exception as exc:
            log.warning("Evaluation LLM call failed: %s", exc)
            eval_conf = max(int(mean_conf * 0.8), 10)
            flag = True
            flag_reason = f"Evaluation failed: {exc}"
            result = {}

        # Apply programmatic flag rules that override LLM
        flag, flag_reason = _apply_flag_rules(
            flag, flag_reason, diagnosis, mean_conf, eval_conf, dissenting,
        )

        eval_result = {
            "diagnosis": diagnosis,
            "eval_confidence": eval_conf,
            "explanation": result.get("explanation", "Evaluation completed."),
            "flag_for_review": flag,
            "flag_reason": flag_reason,
            "confirmed_emergency": False,
        }

        # Build consensus summary for cumulative emission
        consensus_summary = {
            "consensus_diagnosis": diagnosis,
            "mean_confidence": mean_conf,
            "supporting_specialists": args.get("supporting_specialists", "[]"),
            "dissenting_specialists": raw_dissent,
        }

        # Emit stage 4 progress (cumulative — includes prior stages)
        await emit_triage_progress(
            tool_context,
            stage=4,
            stage_name="EVALUATION",
            detail=f"Evaluation complete — confidence {eval_conf}%"
            + (" (flagged for review)" if flag else ""),
            consensus=consensus_summary,
            evaluation=eval_result,
            log_identifier="[TriageEval:Progress]",
        )

        return eval_result


def _evaluate_emergency(diagnosis: str, clinical_note: str) -> dict:
    """Emergency fast-path — validate without consensus cross-reference."""
    from lifesci_common.constants import TRIAGE_EMERGENCY_KEYWORDS

    note_text = clinical_note.lower() if isinstance(clinical_note, str) else ""
    matched = [kw for kw in TRIAGE_EMERGENCY_KEYWORDS if kw in note_text]

    confirmed = len(matched) > 0
    return {
        "diagnosis": diagnosis or "Emergency presentation",
        "eval_confidence": 95 if confirmed else 60,
        "explanation": (
            f"Emergency validated: symptoms match {', '.join(matched[:3])}."
            if confirmed
            else "Emergency flagged but specific symptoms not clearly confirmed in notes."
        ),
        "flag_for_review": not confirmed,
        "flag_reason": None if confirmed else "Emergency classification could not be confirmed from clinical notes.",
        "confirmed_emergency": confirmed,
    }


def _apply_flag_rules(
    flag: bool,
    flag_reason: str | None,
    diagnosis: str,
    mean_conf: float,
    eval_conf: int,
    dissenting: list,
) -> tuple[bool, str | None]:
    """Apply programmatic flag rules that override LLM evaluation."""
    reasons = []
    if flag_reason:
        reasons.append(flag_reason)

    # Rule: mean confidence < 40
    if mean_conf < 40:
        flag = True
        reasons.append(f"Mean specialist confidence ({mean_conf:.0f}%) is below 40%.")

    # Rule: dissenting specialist with confidence > 70
    for d in dissenting:
        if isinstance(d, dict) and d.get("confidence", 0) > 70:
            flag = True
            reasons.append(
                f"Dissenting specialist {d.get('specialist', '?')} suggests "
                f"'{d.get('alternative_diagnosis', '?')}' at {d['confidence']}% confidence."
            )
            break

    # Rule: life-threatening at low eval confidence
    from lifesci_tools.triage_nba import _is_life_threatening

    if _is_life_threatening(diagnosis) and eval_conf < 60:
        flag = True
        reasons.append(
            f"Potentially life-threatening diagnosis at {eval_conf}% confidence."
        )

    return flag, "; ".join(reasons) if reasons else None


_DEFAULT_EVAL_PROMPT = """\
You are a senior medical evaluator. You receive a consensus diagnosis from
a panel of specialist AIs along with structured clinical notes. Assess how
well the diagnosis matches the symptoms described.

Rules:
1. Do NOT change the diagnosis.
2. Cross-reference the diagnosis against known symptom criteria.
3. Flag for review if <50% expected symptoms are present, contradictions exist,
   or the clinical picture doesn't support the diagnosis.

Return ONLY valid JSON:
{
  "eval_confidence": integer 0-100,
  "explanation": "one to two sentences",
  "flag_for_review": true or false,
  "flag_reason": "string or null"
}
"""
