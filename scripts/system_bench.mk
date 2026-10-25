# Bounded benchmark entry point; never echo credentials.
BENCH_MAIN ?= $(abspath ../../a-eye)
ifeq ($(wildcard .env),)
-include $(BENCH_MAIN)/.env
endif
BENCH_PY ?= $(BENCH_MAIN)/.venv/bin/python
PHASE ?= report
.PHONY: system-bench system-bench-test
system-bench:
	@$(BENCH_PY) scripts/system_bench.py --phase $(PHASE)
system-bench-test:
	@$(BENCH_PY) -m pytest -q scripts/test_system_bench.py
	@git diff --check
