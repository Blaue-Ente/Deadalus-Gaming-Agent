"""Harness Core: the common registry, gateway, run ledger and runtime that Studio sits on.

A1 adds the read-only capability registry (``registry``), its hand-written ``manifest`` and
the no-loss ``parity`` table. Nothing else reads them yet, so importing this package has no
effect on behaviour.
"""
