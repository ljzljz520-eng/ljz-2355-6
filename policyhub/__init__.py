"""Enterprise policy / regulation document library.

Subsystems
----------
- clock       injectable time (every rule is evaluated "as-of" a point in time)
- db          sqlite schema (all temporal facts are intervals, never overwritten)
- org         position tree + user assignment history
- engine      request-time applicability evaluator with derivation chains
- catalog     the ONLY module allowed to author policy content / versions
- states      acknowledgement, training, work-authorization (three separate states)
- offline     finite-lived offline attachment grants
- precompute  per-position precomputed applicability sets + drift comparison
- service     facade / visibility boundary
- api         thin JSON HTTP layer
"""
