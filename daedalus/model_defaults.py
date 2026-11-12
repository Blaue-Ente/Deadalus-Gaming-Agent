"""Current default models, in one place (no imports: any module can use them without an import cycle).

2026-11-12: catalogs, presets and defaults had drifted two model generations behind, each a copy of a literal id.
scripts/check_model_ids.py lists ids in the code that the provider no longer lists.
"""
OPENROUTER_DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"     # the brain when the owner picked none
OPENROUTER_FAST_MODEL = "anthropic/claude-haiku-5.5"         # quick checks (review gate, fast slots)
OPENROUTER_DEEP_MODEL = "anthropic/claude-opus-5.5"          # deep slots in the premium presets
