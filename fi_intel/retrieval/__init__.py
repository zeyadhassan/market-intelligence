"""Retrieval over the licensed corpus.

Entitlement filtering happens in the data layer (invariant 3): every query
joins the source registry and entitlement grants and filters by the
caller's group and barrier side. A prompt-level restriction is not a
control, and there is deliberately no way to ask this package for an
unfiltered query.
"""
