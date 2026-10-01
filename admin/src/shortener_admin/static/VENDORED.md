# Vendored front-end assets (no CDN at runtime, spec §8)

| File | Version | Source | SHA-256 |
|---|---|---|---|
| vendor/htmx.min.js | 2.0.4 | https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |
| vendor/pico.min.css | 2.0.6 | https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css | `dd5fd5591afd81ee21dcc117ad85c014dc3f1f19dc2d7b7d101ea0acc29274c2` |
| vendor/chart.umd.js | 4.4.7 | https://unpkg.com/chart.js@4.4.7/dist/chart.umd.js | `2812cb8825fdc57469eb2f7bb055e9429244e599920511ee477e828499b632cb` |

Upgrade: download the new version, update the table, and run `make check`
(`test_vendored_assets_match_recorded_hashes` verifies every row).
