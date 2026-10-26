"""The tests of the wizard's older behaviour run without the root index (``lex.sqlite``) and the semantic index: they
would otherwise change with whatever is built on the machine. Tests of those build their own small ones and switch
``config.LEX_DB`` / ``config.VEC_DIR`` for the length of the test (see tests/test_icm_lex.py).

Open mode (every visitor an admin) is off for all tests: ``icm.config`` reads it when it is first imported, which any
module (``icm.align`` imports ``icm.lex``) may do before a test module sets ``ICM_OPEN`` itself."""
import os

os.environ.setdefault("ICM_LEX_DB", "/nonexistent/lex.sqlite")
os.environ.setdefault("ICM_VEC_DIR", "/nonexistent/vec")
os.environ.setdefault("ICM_OPEN", "0")
