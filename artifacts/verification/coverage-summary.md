commit=5353cc112d5fb2995fcb32d6351b2ab104fc283d
command=uv run pytest -m "not external" --cov=trading_platform --cov-report=term-missing --cov-report=xml:artifacts/verification/coverage.xml
utc=2026-09-16T17:15:46.120572+00:00
platform=nt
python=Python 3.14.3
uv=uv 0.10.9 (f675560f3 2026-03-06)
exit_code=0

........................................................................ [ 93%]
.....                                                                    [100%]
=============================== tests coverage ================================
_______________ coverage: platform win32, python 3.14.3-final-0 _______________

Name                                                                          Stmts   Miss  Cover   Missing
-----------------------------------------------------------------------------------------------------------
trading-platform\src\trading_platform\__init__.py                                 0      0   100%
trading-platform\src\trading_platform\authorization.py                           29      2    93%   34, 56
trading-platform\src\trading_platform\backup.py                                  23      3    87%   33, 42, 45
trading-platform\src\trading_platform\broker_adapter.py                         101     26    74%   92, 123, 127, 145-148, 156, 163-165, 204-222, 260, 263, 269, 273
trading-platform\src\trading_platform\chaos_engine.py                           153     24    84%   119, 142-144, 156-158, 163-172, 190, 199-201, 245, 249, 251, 255, 257, 311, 314-315, 335
trading-platform\src\trading_platform\cli\__init__.py                            10     10     0%   3-20
trading-platform\src\trading_platform\config.py                                  15      0   100%
trading-platform\src\trading_platform\data\__init__.py                          101     21    79%   64, 68, 72, 76, 91, 95, 99, 103, 147, 174-175, 179, 198, 201, 204, 207, 210, 213, 232-233, 249
trading-platform\src\trading_platform\data\ingestion\__init__.py                  2      0   100%
trading-platform\src\trading_platform\data\ingestion\daily_bar_ingestion.py      82     14    83%   58, 76, 91-92, 97, 117-121, 132, 173, 184, 218
trading-platform\src\trading_platform\domain\__init__.py                        207     10    95%   26, 33, 59, 61, 115, 155, 157, 207, 259, 264
trading-platform\src\trading_platform\features\__init__.py                       38      1    97%   56
trading-platform\src\trading_platform\ml_ranking.py                             196     39    80%   59-61, 64, 83-85, 235, 237, 239, 241, 261-267, 309, 326-327, 344-345, 363-364, 374-375, 378-379, 383-390, 435, 460-465, 485
trading-platform\src\trading_platform\monitor.py                                135     21    84%   76, 109-113, 118, 178-182, 188, 207-208, 254, 291-293, 298-300, 315, 322, 326, 330, 433
trading-platform\src\trading_platform\observability\__init__.py                  14      1    93%   22
trading-platform\src\trading_platform\oms\oms.py                                216     21    90%   55, 94-95, 155, 217, 267, 338-340, 356, 370-371, 412-413, 432, 446, 548, 562-565
trading-platform\src\trading_platform\persistence\__init__.py                     0      0   100%
trading-platform\src\trading_platform\persistence\baseline_report.py            160     21    87%   43, 60, 64, 74, 79, 88, 153, 236, 246-256, 259, 300, 302
trading-platform\src\trading_platform\persistence\experiment.py                 112     31    72%   91-94, 113-126, 215, 223-225, 238, 242, 246-256, 260
trading-platform\src\trading_platform\persistence\postgres.py                   130     29    78%   80-81, 97, 108-109, 158-159, 183-184, 208-209, 224, 235-238, 249-250, 275-276, 292-293, 315-316, 335-336, 360-361, 373
trading-platform\src\trading_platform\reconciliation\__init__.py                 12      0   100%
trading-platform\src\trading_platform\risk\__init__.py                            0      0   100%
trading-platform\src\trading_platform\risk\limits.py                             73     11    85%   48, 89, 97, 186, 190, 199, 203, 211, 215, 220, 224
trading-platform\src\trading_platform\risk\risk_engine.py                       364     36    90%   72, 132, 188-189, 191-192, 208-220, 270-272, 312, 316, 334, 420-421, 613-615, 632-634, 648-650, 880-881, 902-903, 980, 1029
trading-platform\src\trading_platform\risk__init__.py                             0      0   100%
trading-platform\src\trading_platform\shadow.py                                  23      0   100%
trading-platform\src\trading_platform\simulator\event_driven_simulator.py       257     32    88%   339, 453, 466-467, 506, 511-514, 533, 542-558, 579-580, 593-597, 663, 667, 671, 675, 679
trading-platform\src\trading_platform\strategies\ma_cross_strategy.py            40      7    82%   58, 87, 93, 99, 104, 110, 130
trading-platform\src\trading_platform\walk_forward\walk_forward.py              183     40    78%   87, 130, 226-236, 245-255, 263-273, 322-326, 338-340, 355-367, 398
-----------------------------------------------------------------------------------------------------------
TOTAL                                                                          2676    400    85%
Coverage XML written to file artifacts/verification/coverage.xml
77 passed in 8.24s
