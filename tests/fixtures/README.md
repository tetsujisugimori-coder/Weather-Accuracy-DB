# JMA forecast fixtures

- Source structure: `https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json`
- Area master: `https://www.jma.go.jp/bosai/common/const/area.json`
- Structure checked: 2026-09-01 (Japan time)

The values and dates are fixed test data derived from the official JSON structure. The
05:00, 11:00, and 17:00 short-term fixtures intentionally vary the number and ordering
of temperature timestamps. `00:00` means a daily minimum and `09:00` a daily maximum;
the parser classifies them by Japan-time timestamps, not by array position. Tests never
fetch these fixtures from the network.
