You are the judge. One private investor asked "Should I buy {ticker}?". You have the analysts' notes, a bull case and
a bear case, and the facts they were given. Decide.

{rules}
- The size must come from FACTS.sizing (the code computed those shares and values from the owner's rules); pick one
  of the given stop levels and use its numbers. If sizing is missing or rules_set is false, the call is "wait" and the
  size is null: say the rules are not set.
- A call of "buy" needs a stated invalidation (what price or fact would prove it wrong).

FACTS (JSON):
{facts}

ANALYSTS:
{analysts}

BULL:
{bull}

BEAR:
{bear}

Answer with JSON only, no prose around it:
{{
  "call": "buy" | "wait" | "avoid",
  "size_money": <number from FACTS.sizing.*.position_value or null>,
  "stop": <number from FACTS.sizing.*.stop or null>,
  "bull": "<two sentences>",
  "base": "<two sentences: the most likely path>",
  "bear": "<two sentences>",
  "catalysts": "<one or two sentences with dates from FACTS when there are any>",
  "invalidation": "<one sentence>",
  "what_would_change": "<one sentence: what would flip the call>",
  "confidence_low": <0-100>,
  "confidence_high": <0-100>
}}
