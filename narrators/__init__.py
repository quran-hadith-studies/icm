"""Narrator metadata database: Noor RaviIds linked to rijāl biographies and Sunni sets.

Stages (see ``cli.py``): noor → rijal → wikishia → thaqalayn → link → facts → sunni → export → eval.
Everything is written to one SQLite file (``external/narrators.sqlite``) whose schema is
``schema.sql``; each stage drops and recreates only its own tables.
"""
