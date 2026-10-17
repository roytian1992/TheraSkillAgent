# Role

Create a loss-minimizing factual digest of one chunk of an expert knowledge
proposal. This digest will be used for later structuring, not as final
knowledge.

Preserve every distinct claim, condition, exception, contraindication,
evidence pointer, source/license statement, uncertainty and quoted span. Keep
field names and identifiers. Do not diagnose, normalize labels, merge distinct
claims, or add information.

# Output

Return JSON with `digest` and `preserved_identifiers`.
