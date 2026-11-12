# Measured Load Test Results

SLO target: **first token within 1500 ms** of first send (retries included).

| Concurrent Users | Messages | SLO Attainment | p50 TTFT (ms) | p95 TTFT (ms) | 503 Retries | Errors |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 5 | 15 | 100.0% | 605.4 | 616.9 | 0 | 0 |
| 10 | 30 | 100.0% | 837.5 | 842.1 | 0 | 0 |
| 15 | 45 | 80.0% | 835.0 | 1554.3 | 41 | 3 |
| 20 | 60 | 70.0% | 839.1 | 1601.5 | 94 | 11 |
| 25 | 75 | 56.0% | 852.8 | 1616.9 | 163 | 22 |
| 30 | 90 | 41.1% | 1091.8 | 1712.6 | 246 | 32 |
