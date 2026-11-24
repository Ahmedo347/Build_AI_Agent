# Eval Report: gemini-3.5-flash-lite

**Date:** 2026-11-23T21:19:42.038646Z
**Requested Model:** gemini-3.5-flash-lite
**Actual Models Seen:** gemini-3.5-flash-lite
**Total Tasks:** 16
**Passed:** 16 | **Failed:** 0 | **Errors:** 0 | **Skipped:** 0
**Pass Rate:** 100.0%
**Average Turns:** 9.4
**Total Prompt Tokens:** 325,894
**Total Completion Tokens:** 17,361
**Total Tokens:** 343,255
**Total Latency:** 738.8s
**Total Rate Limit Wait:** 674.3s

## Per-Task Results

| ID | Category | Status | Turns | Tokens | Latency (s) | Drift | Notes |
|----|----------|--------|-------|--------|-------------|-------|-------|
| read-01 | read | ✅ pass | 2 | 1,554 | 5.8 |  | verify: no verify_cmd; will check expected_substrings; content: all 6 substrings present |
| read-02 | read | ✅ pass | 4 | 3,184 | 13.8 |  | verify: no verify_cmd; will check expected_substrings; content: all 7 substrings present |
| read-03 | read | ✅ pass | 5 | 4,861 | 25.3 |  | verify: no verify_cmd; will check expected_substrings; content: all 4 substrings present |
| read-04 | read | ✅ pass | 4 | 4,900 | 33.7 |  | verify: no verify_cmd; will check expected_substrings; content: all 5 substrings present |
| read-05 | read | ✅ pass | 5 | 4,903 | 18.8 |  | verify: no verify_cmd; will check expected_substrings; content: all 3 substrings present |
| fix-01 | fix | ✅ pass | 9 | 18,376 | 44.0 |  | verify: verify_cmd exited 0 |
| fix-02 | fix | ✅ pass | 13 | 32,083 | 60.7 |  | verify: verify_cmd exited 0 |
| fix-03 | fix | ✅ pass | 14 | 33,126 | 60.1 |  | verify: verify_cmd exited 0 |
| feature-01 | feature | ✅ pass | 12 | 27,029 | 53.1 |  | verify: verify_cmd exited 0 |
| feature-02 | feature | ✅ pass | 11 | 21,540 | 63.6 |  | verify: verify_cmd exited 0 |
| feature-03 | feature | ✅ pass | 10 | 18,830 | 42.0 |  | verify: verify_cmd exited 0 |
| feature-04 | feature | ✅ pass | 17 | 56,580 | 78.7 |  | verify: verify_cmd exited 0 |
| feature-05 | feature | ✅ pass | 16 | 48,875 | 70.1 |  | verify: verify_cmd exited 0 |
| read-06 | read | ✅ pass | 2 | 1,459 | 13.4 |  | verify: no verify_cmd; will check expected_substrings; content: all 5 substrings present |
| feature-06 | feature | ✅ pass | 12 | 24,961 | 70.6 |  | verify: verify_cmd exited 0 |
| feature-07 | feature | ✅ pass | 15 | 40,994 | 70.4 |  | verify: verify_cmd exited 0 |
