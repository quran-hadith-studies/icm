"""narwiki: a wiki-like narrator (rijal) database served at /narrator.

One *person* per narrator, carrying external ids in any number of source databases (``noor`` = Noor's
RaviIds in ``narrators.sqlite``, ``kg`` = the Hadith Knowledge Graph's rawis in ``kg.sqlite``, ``wiki`` =
persons created in the wiki itself; a further database is just another kind). Every field keeps its
provenance (source database or the revision that wrote it). Every change is applied at once and recorded
as a revision that a reviewer later approves or rejects; automatic matching only produces suggestions.

Modules: ``config`` (paths), ``store`` (the writable database), ``lookup`` (read-only API for ICM),
``seed`` and ``similar`` (laptop-side builders), ``api`` (FastAPI router), ``cli``.
"""
