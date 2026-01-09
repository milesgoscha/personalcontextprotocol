"""
PCP - Personal Context Protocol

A protocol for representing and exchanging personal context with AI agents.

This package exposes both the specification version (meaning-level contract)
and the implementation version (reference node + tooling).
"""

__version__ = "0.1.0"
SPEC_VERSION = "0.2-draft"
CONFORMANCE_LEVEL = "PCP-Extended"
SPEC_REFERENCES = {
    "capabilities": "PCP §5",
    "scope": "PCP §6",
    "temporal": "PCP §7",
    "identity": "PCP §8",
    "audit": "PCP §9",
    "transport": "PCP §10",
}
