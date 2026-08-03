"""Test-only helpers for Phase 2I-A.1 canonical configuration migration.

Production consumers now read ``runtime_config.<domain>.<subdomain>.<field>``
directly. These helpers build canonical-shaped test doubles so unit tests can
inject targeted overrides without booting the full ``ApexVoidConfig``.
"""
