# Next Best Action (NBA) — Reference Documentation

The NBA engine determines the appropriate level of care for each triaged
patient. It uses deterministic decision rules (no LLM) based on the
evaluation results from the specialist panel and evaluator.

## Role

Medical routing advisor. Receives the diagnosis, confidence scores,
evaluation flags, and emergency status. Outputs a routing recommendation
with urgency level and supporting rationale.

## Decision Rules

Routes are evaluated in priority order. The **first matching rule** wins.

### 1. Emergency Services (`emergency_services`)

**Condition**: `emergency_override == true`

- Urgency: `immediate`
- Reasoning: Emergency symptoms detected during intake. Requires immediate
  emergency intervention.
- Action: Direct to emergency services (911/999/112).

### 2. Hospital — Life-Threatening + Flagged (`hospital`)

**Condition**: `flag_for_review == true` AND diagnosis matches a
life-threatening condition

- Urgency: `immediate`
- Reasoning: Potentially life-threatening condition flagged for review.
- Action: Direct to hospital emergency department.

### 3. Hospital — Low Confidence + Flagged (`hospital`)

**Condition**: `flag_for_review == true` AND `mean_confidence < 40`

- Urgency: `urgent`
- Reasoning: Low diagnostic confidence with review flag indicates uncertain
  but potentially serious condition.
- Action: Direct to hospital for professional evaluation.

### 4. GP Visit — Flagged (`gp_visit`)

**Condition**: `flag_for_review == true` (other flag cases)

- Urgency: `urgent`
- Reasoning: Case flagged for clinical review; GP evaluation recommended.
- Action: Schedule urgent GP appointment.

### 5. Hospital — Life-Threatening + Low Eval Confidence (`hospital`)

**Condition**: Diagnosis is life-threatening AND `eval_confidence < 60`

- Urgency: `urgent`
- Reasoning: Potentially life-threatening condition with moderate evaluator
  confidence. Hospital evaluation warranted for safety.
- Action: Direct to hospital.

### 6. GP Visit — Inconclusive (`gp_visit`)

**Condition**: `consensus_diagnosis == "INCONCLUSIVE"`

- Urgency: `urgent`
- Reasoning: Specialist panel could not reach consensus. Professional
  evaluation recommended to establish diagnosis.
- Action: Schedule urgent GP appointment.

### 7. Self Care (`self_care`)

**Condition**: `eval_confidence >= 75` AND diagnosis is NOT life-threatening
AND diagnosis matches self-care eligible conditions (common cold, tension
headache, muscle strain, minor sprain, mild allergic rhinitis, etc.)

- Urgency: `low`
- Reasoning: High confidence for a mild, self-limiting condition.
- Action: Provide self-care instructions, hydration advice, and escalation
  criteria.
- Follow-up: See a doctor if not improving within 2 weeks.

### 8. Pharmacist (`pharmacist`)

**Condition**: `eval_confidence >= 70` AND diagnosis matches OTC-eligible
conditions (acid reflux, mild eczema, contact dermatitis, hemorrhoids,
athlete's foot, mild conjunctivitis, heartburn, etc.)

- Urgency: `low`
- Reasoning: Minor condition with high confidence. Over-the-counter
  treatment likely sufficient.
- Action: Recommend pharmacist consultation for OTC remedies.

### 9. Specialist Referral (`specialist_referral`)

**Condition**: `specialist_type` is specified AND `eval_confidence >= 60`

- Urgency: `routine`
- Reasoning: Condition requires specialist management.
- Action: Book specialist appointment within 2 weeks.
- Follow-up timeframe: 2 weeks.

### 10. GP Visit — Moderate Confidence (`gp_visit`)

**Condition**: `eval_confidence >= 60` (catch-all for moderate confidence)

- Urgency: `routine`
- Reasoning: Moderate-to-high confidence. GP evaluation recommended.
- Action: Schedule routine GP appointment.

### 11. GP Visit — Default (`gp_visit`)

**Condition**: None of the above matched (fallback)

- Urgency: `urgent`
- Reasoning: Low confidence. Professional evaluation needed.
- Action: Schedule urgent GP appointment.

## Output Format

The NBA tool returns a JSON object:

```json
{
  "diagnosis": "string — the consensus diagnosis",
  "route": "emergency_services | hospital | specialist_referral | gp_visit | pharmacist | self_care",
  "urgency": "immediate | urgent | routine | low",
  "color": "red | orange | yellow | blue | green | gray",
  "specialist_type": "string or null — specialist for referral route",
  "reasoning": "string — explanation of why this route was chosen",
  "self_care_instructions": "string or null — home care guidance",
  "escalation_criteria": "string or null — when to seek higher care",
  "follow_up_timeframe": "string or null — recommended follow-up window",
  "disclaimer": "This is an AI-assisted triage suggestion..."
}
```

## Life-Threatening Conditions

The following diagnoses trigger life-threatening escalation logic:

- Myocardial infarction / heart attack
- Stroke
- Pulmonary embolism
- Anaphylaxis
- Sepsis / septic shock
- Aortic dissection
- Pneumothorax
- Status epilepticus
- Meningitis
- Diabetic ketoacidosis (DKA)
- Acute abdomen
