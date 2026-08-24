"""Agent tools: the only capabilities the research agent may invoke.

These replace upstream's web search. Every tool reads from our corpus or
our graph, through the entitlement-checked retrieval service. There is no
open-internet tool — the source restriction is structural (invariant 2),
not prompted.
"""
