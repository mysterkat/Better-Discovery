"""Thin adapters around the read-only MONTE CARLO/src toolkit.

Nothing in this package modifies the existing code. Where a function signature
is parameterless in the source (e.g. pattern_discovery_v6.main), the bridge
monkey-patches the imported module's globals at runtime rather than editing
the file on disk.
"""
