"""Deterministic, service-free proof-of-concept vertical slice.

The demo is deliberately separate from production wiring. It exercises the
same domain contracts with packaged fixtures and explicitly labelled local
heuristics so a developer can evaluate the product loop without databases or
an external model endpoint.
"""

from fi_intel.demo.runner import POCDemoArtifacts, run_poc_demo

__all__ = ["POCDemoArtifacts", "run_poc_demo"]
