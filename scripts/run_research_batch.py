#!/usr/bin/env python3
"""Run Phase 1 research for a single batch file using direct search API calls.

Usage: python3 run_research_batch.py <batch_name> [run_dir]
  batch_name — e.g. research_batch0 (file is cache/research_batch0.json)
  run_dir    — e.g. 20260530_1240_deep (folder under logs/); reads .current_run if omitted

Reads web.backend from ~/.hermes/config.yaml to pick the provider,
then reads the matching key from ~/.hermes/.env. Switch providers in
config.yaml — no code change needed.
"""
import json, sys, time, subprocess, datetime, re, urllib.parse
from pathlib import Path

HERMES = Path('/home/shaah/.hermes')

BACKEND_ENV_VAR = {
    'brave':  'BRAVE_SEARCH_API_KEY',
    'tavily': 'TAVILY_API_KEY',
}

def _read_env(key):
    with open(HERMES / '.env') as f:
        for line in f:
            parts = line.strip().split('=', 1)
            if len(parts) == 2 and parts[0].strip() == key:
                return parts[1].strip().strip('"').strip("'")
    return None

def _read_config_backend():
    text = (HERMES / 'config.yaml').read_text()
    m = re.search(r'^web:\n(?:[ \t]+\S[^\n]*\n)*?[ \t]+backend:\s*(\S+)', text, re.MULTILINE)
    return m.group(1).lower() if m else 'tavily'

backend = _read_config_backend()
env_var = BACKEND_ENV_VAR.get(backend)
if not env_var:
    print(f"ERROR: Unknown search backend '{backend}' (supported: {list(BACKEND_ENV_VAR)})")
    sys.exit(1)

api_key = _read_env(env_var)
if not api_key:
    print(f"ERROR: {env_var} not found in ~/.hermes/.env")
    sys.exit(1)

print(f"Search: {backend}, key: ...{api_key[-8:]}", flush=True)

def do_search(query, max_results=5):
    if len(query) > 400:
        query = query[:397] + '...'
    if backend == 'brave':
        q = urllib.parse.quote(query)
        result = subprocess.run(
            ['curl', '-s',
             f'https://api.search.brave.com/res/v1/web/search?q={q}&count={max_results}',
             '-H', f'X-Subscription-Token: {api_key}',
             '-H', 'Accept: application/json'],
            capture_output=True, text=True, timeout=15)
        try:
            data = json.loads(result.stdout)
            if 'type' not in data:
                return None, result.stdout[:200]
            items = [{'url': r.get('url', ''), 'title': r.get('title', '')}
                     for r in data.get('web', {}).get('results', [])
                     if r.get('url') and r.get('title')]
            return items, None
        except Exception as e:
            return None, str(e)[:200]
    else:  # tavily
        payload = json.dumps({'api_key': api_key, 'query': query, 'max_results': max_results})
        result = subprocess.run(
            ['curl', '-s', '-X', 'POST', 'https://api.tavily.com/search',
             '-H', 'Content-Type: application/json', '-d', payload],
            capture_output=True, text=True, timeout=15)
        try:
            data = json.loads(result.stdout)
            if 'detail' in data:
                return None, str(data['detail'])[:200]
            items = [{'url': r.get('url', ''), 'title': r.get('title', '')}
                     for r in data.get('results', [])
                     if r.get('url') and r.get('title')]
            return items, None
        except Exception as e:
            return None, str(e)[:200]

def extract_domain(url):
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.replace('www.', '')
    except:
        return ''

def short_title(title, max_words=8):
    t = title.replace('?', '').strip()
    return ' '.join(t.split()[:max_words])

REPO = Path('/home/shaah/kalshi-tracker')
batch_name = sys.argv[1]
batch_path = REPO / 'cache' / f'{batch_name}.json'

if len(sys.argv) >= 3:
    run_dir = REPO / 'logs' / sys.argv[2]
else:
    pointer = REPO / 'logs' / '.current_run'
    if pointer.exists():
        run_dir = REPO / 'logs' / pointer.read_text().strip()
    else:
        print("ERROR: no run_dir arg and no .current_run pointer", file=sys.stderr)
        sys.exit(1)

data = json.loads(batch_path.read_text())
print(f"Loaded {len(data)} candidates", flush=True)

ok_count = empty_count = consecutive_zeros = 0
failed_at = None

for i, entry in enumerate(data):
    if failed_at is not None:
        entry['research'] = {'searches_performed': [], 'findings': [],
            'summary': 'Search failed', 'search_status': 'search_failed', 'batch': batch_name}
        continue

    ticker = entry['ticker']
    short = short_title(entry['title'])
    searches, all_findings = [], []

    current_month = datetime.date.today().strftime('%B %Y')
    for tmpl in ['{} news ' + current_month, '{} current status 2026', '{} outcome result']:
        q = tmpl.format(short)
        searches.append(q)
        results, err = do_search(q)
        if err:
            print(f"  [{ticker}] {err[:60]}", file=sys.stderr)
        elif results:
            for r in results:
                url, title_r = r.get('url', ''), r.get('title', '')
                if url and title_r:
                    all_findings.append({'source': extract_domain(url), 'url': url, 'detail': title_r})
        time.sleep(0.1)

    seen, findings = set(), []
    for f in all_findings:
        if f['url'] not in seen:
            seen.add(f['url'])
            findings.append(f)

    n = len(findings)
    if n > 0:
        ok_count += 1
        consecutive_zeros = 0
        status = 'ok'
    else:
        empty_count += 1
        consecutive_zeros += 1
        if consecutive_zeros >= 3:
            failed_at = i
            print(f"FAIL at {i} ({ticker}): 3 consecutive zeros", flush=True)
            entry['research'] = {'searches_performed': searches, 'findings': [],
                'summary': f'Failed: {short[:60]}', 'search_status': 'search_failed', 'batch': batch_name}
            continue
        status = 'empty'

    summary = (f"Found {n} result(s). Top: {findings[0]['detail'][:80]}" if findings
               else f"No news: {short[:60]}")
    entry['research'] = {'searches_performed': searches, 'findings': findings[:6],
        'summary': summary, 'search_status': status}

    print(f"  [{i+1}/{len(data)}] {ticker:<40} ok={ok_count} empty={empty_count}", flush=True)

batch_path.write_text(json.dumps(data, indent=2))
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / f'{batch_name}.json').write_text(json.dumps(data, indent=2))
print(f"Saved {batch_name}: {ok_count} ok, {empty_count} empty", flush=True)
if failed_at is not None:
    print(f"  STOPPED at candidate {failed_at}")
