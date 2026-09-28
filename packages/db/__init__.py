"""Database access package: engines, sessions, and repositories.

Layered architecture enforced by package layout::

    Service -> Repository -> (canonical engine | shard engine manager)
"""
