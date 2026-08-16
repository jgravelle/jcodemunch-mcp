# description_prompt.py

DESCRIPTION_QUALITY_SYSTEM_PROMPT = (
    "You are grading MCP tool descriptions with a strict rubric. "
    "Score the description conservatively and return only valid JSON matching the requested schema."
)

DESCRIPTION_QUALITY_SYSTEM_PROMPT_WITH_SOURCE = (
    "You are grading MCP tool descriptions with a strict rubric. "
    "When SOURCE_CODE is provided, use it as grounding while scoring: verify whether the description "
    "matches the tool's actual behavior, parameters, return values, side effects, and limitations. "
    "Do not invent details that are not supported by the description or the source code. "
    "Return only valid JSON matching the requested schema."
)

DESCRIPTION_QUALITY_PROMPT = r"""
Judge Tool Description Quality Using 6-Component Rubric

You are grading a tool description. Score each component from 1-5, then provide an
overall quality score (0-100), justification, and improvement recommendations.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING RUBRIC (1-5 scale for each component)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Purpose (What the tool does)
   - 5/5: Clearly explains function, behavior, and return data with precise language
   - 4/5: Explains function and behavior with minor ambiguity
   - 3/5: Basic explanation present but lacks behavioral details
   - 2/5: Vague or incomplete purpose statement
   - 1/5: Purpose unclear or missing

2. Usage Guideline (When to use/not use)
   - 5/5: Explicitly states appropriate use cases AND when NOT to use; includes
          disambiguation if tool name is ambiguous
   - 4/5: States when to use with minimal guidance on when not to use
   - 3/5: Implies usage context but lacks explicit boundaries
   - 2/5: Usage context unclear or overly generic
   - 1/5: No usage guidance provided

3. Limitation (Caveats and boundaries)
   - 5/5: Clearly states what tool does NOT return, scope boundaries, and any
          important constraints
   - 4/5: Mentions main limitations but misses some edge cases
   - 3/5: Vague or incomplete limitation statements
   - 2/5: Minimal or implied limitations only
   - 1/5: No limitations or caveats mentioned

4. Parameter Explanation (Input clarity)
   - 5/5: Every parameter explained with type, meaning, effect on behavior, and
          required/default status
   - 4/5: Most parameters explained with minor omissions
   - 3/5: Basic parameter info present but lacks behavioral impact
   - 2/5: Parameters listed without meaningful explanation
   - 1/5: Parameters not explained or only in schema

5. Examples vs. Description Balance
   - 5/5: Description is self-sufficient; examples (if any) supplement, not replace,
          explanation
   - 4/5: Mostly descriptive with minor reliance on examples
   - 3/5: Even mix of description and examples
   - 2/5: Over-relies on examples with minimal prose
   - 1/5: Only examples, no descriptive explanation

6. Length and Completeness
   - 5/5: 4+ sentences of substantive, well-structured prose covering all aspects
   - 4/5: 3-4 sentences with good coverage
   - 3/5: 2-3 sentences, somewhat complete
   - 2/5: 1-2 sentences, too brief
   - 1/5: Single phrase or fragment

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{tool_payload}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT (JSON)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{
  "scores": {
    "purpose": 1-5,
    "usage_guideline": 1-5,
    "limitation": 1-5,
    "parameter_explanation": 1-5,
    "examples_balance": 1-5,
    "length_completeness": 1-5
  },
  "overall_quality_score": 0-100,
  "label": "Good" | "Bad",
  "reason": "One sentence justification",
  "improvement_needed": ["comma separated list of specific weak areas with scores <= 3"]
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING GUIDELINES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

How to calculate overall_quality_score (0-100):

Step 1: Score each of the 6 components on 1-5 scale
Step 2: Sum all 6 scores (range: 6-30)
Step 3: Convert to 0-100 scale using:
   overall_quality_score = ((sum_of_scores - 6) / 24) * 100

Label:

A description is Bad if:
- Any of the six rubric dimensions score below 3, or
- Examples replace the description instead of supporting it.

A description is Good only if:
- All six dimensions score 3 or higher, and
- Requirements in points 1 through 6 are satisfied.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FEW-SHOT EXAMPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

... (keep your examples here unchanged)
"""
