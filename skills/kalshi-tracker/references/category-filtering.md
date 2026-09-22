# Category Filtering by Platform

## Kalshi (step_1_scan/scanner.py + step_1_scan/anomaly_scanner.py)

Both `ScannerAgent` and `AnomalyScanner` use the same `scan_categories` list from `DEFAULT_CONFIG`:

```python
"scan_categories": ["Politics", "Economics", "Entertainment", "Weather", "World", "Elections"]
```

Events in any of these 6 categories are included. Everything else is excluded. The Kalshi API's event-level `category` field is used for filtering.

The Kalshi API categories include roughly 10+ categories (Sports, Health, Technology, Weather, etc.) but only these 6 are scanned.

## Polymarket (step_1_scan/polymarket_scanner.py)

### Scan categories
```python
"scan_categories": ["Politics", "Economics", "Entertainment", "World", "Science"]
```

Events must match one of these 5 categories to be included.

### Category mapping (shared/polymarket_client.py CATEGORY_MAP)

Polymarket's Gamma API stores categories as tags. The `CATEGORY_MAP` normalizes raw tags to internal names:

| Raw tag(s) | Mapped to | Status |
|------------|-----------|--------|
| Politics | Politics | Included |
| Business & Finance, Finance, Economics, Economy, Business, Markets | Economics | Included |
| Entertainment & Pop Culture, Pop Culture, Entertainment, Arts & Entertainment | Entertainment | Included |
| World, News | World | Included |
| Science & Technology, Science, Technology | Science | Included |
| Sports | None | **Excluded** |
| Crypto, Cryptocurrency | None | **Excluded** |
| Anything not in map | None | **Excluded** |

Markets where `CATEGORY_MAP` returns `None` are skipped at the scanner level (line 120-121 of step_1_scan/polymarket_scanner.py):

```python
# Skip markets where category was not mappable (None = excluded category)
if market.get("category") is None:
    continue
```

## Shared categories across platforms

| Category | Kalshi | Polymarket |
|----------|--------|------------|
| Politics | ✅ | ✅ |
| Economics | ✅ | ✅ |
| Entertainment | ✅ | ✅ |
| World | ✅ | ✅ |
| Weather | ✅ | ❌ |
| Elections | ✅ | ❌ |
| Science | ❌ | ✅ |
| Sports | ❌ | ❌ |
| Crypto | ❌ | ❌ |
