#!/usr/bin/env python3
"""Individual donors by employer, and super PAC funders, from the FEC API.

Needs an API key (free: https://api.data.gov/signup/) in the FEC_API_KEY environment variable.
Runs inside the GitHub workflow; fec.gov is not reachable from every environment.

  --races FILE      docs/data/races.json (which candidates, which committees)
  --out FILE        docs/data/employers.json
  --cache FILE      docs/data/employers_cache.json (kept between runs; a committee is refreshed every --ttl days)
  --ttl DAYS        default 3
  --max-calls N     stop after N API calls this run and leave the rest for the next run (default 900: the key allows 1,000/hour)
  --superpacs N     also pull the top N outside-spending committees' largest donors (default 40)

For every nominee: the FEC's list of committees linked to the candidate (principal, authorized, leadership PAC,
joint fundraiser), and for each of those the FEC's own aggregation of itemized receipts by donor employer
(/schedules/schedule_a/by_employer/). Employers are tagged with the same industry rules as the PAC file.
For the biggest outside groups: their largest itemized receipts (/schedules/schedule_a/), i.e. who funds them.
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from industries import classify_employer, EMP_LABELS

ap = argparse.ArgumentParser()
ap.add_argument('--races', required=True); ap.add_argument('--out', required=True); ap.add_argument('--cache')
ap.add_argument('--ttl', type=int, default=3); ap.add_argument('--max-calls', type=int, default=900); ap.add_argument('--superpacs', type=int, default=40)
ap.add_argument('--cycle', type=int, default=2026)
A = ap.parse_args()
KEY = os.environ.get('FEC_API_KEY', '').strip()
if not KEY:
    print('FEC_API_KEY not set; writing nothing and leaving any existing employers.json alone.'); sys.exit(0)

BASE = 'https://api.open.fec.gov/v1'
calls = 0
def get(path, **params):
    """One API call with the key, polite pacing, and retry on rate limits. Returns parsed JSON or None."""
    global calls
    if calls >= A.max_calls: raise RuntimeError('call budget exhausted')
    params['api_key'] = KEY
    url = f'{BASE}{path}?' + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(5):
        try:
            calls += 1
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'follow-the-ads-2026'}), timeout=60) as r:
                data = json.load(r)
            time.sleep(3.7)   # ~970 calls/hour, under the 1,000/hour limit
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(60 * (attempt + 1)); continue
            if e.code in (500, 502, 503, 504): time.sleep(10); continue
            print('HTTP', e.code, url.replace(KEY, 'KEY')); return None
        except Exception as e:
            print('error', e, url.replace(KEY, 'KEY')); time.sleep(5)
    return None

def paged(path, limit_pages=1, **params):
    out = []
    for page in range(1, limit_pages + 1):
        d = get(path, page=page, **params)
        if not d: break
        out += d.get('results', [])
        if page >= (d.get('pagination', {}).get('pages') or 1): break
    return out

races = json.load(open(A.races))
cache = {}
if A.cache and os.path.exists(A.cache):
    cache = json.load(open(A.cache))
now = datetime.now(timezone.utc)
def fresh(entry):
    return entry and (now - datetime.fromisoformat(entry['fetched'])) < timedelta(days=A.ttl)

nominees = [(c, r) for r in races['races'] for c in r['cands'] if c.get('nominee')]
# biggest spenders first, so a budget-limited run covers the races people look at
nominees.sort(key=lambda cr: -cr[1]['total'])

out_cands = {}
done = skipped = stale = 0
budget_out = False

# ---- super PAC funders first (cheap, and the most-asked question)
sp = {}
for cm in (races['meta'].get('top_committees') or [])[:A.superpacs]:
    mid = cm['id']; ent = cache.get('sp:' + mid)
    if not fresh(ent) and not budget_out:
        try:
            rows = paged('/schedules/schedule_a/', limit_pages=1, committee_id=mid, two_year_transaction_period=A.cycle, sort='-contribution_receipt_amount', per_page=100)
            ent = {'fetched': now.isoformat(), 'donors': [{'name': x.get('contributor_name'), 'employer': x.get('contributor_employer') or '', 'occupation': x.get('contributor_occupation') or '',
                                                           'amount': round(x.get('contribution_receipt_amount') or 0, 2), 'date': (x.get('contribution_receipt_date') or '')[:10],
                                                           'type': x.get('entity_type') or '', 'image': x.get('image_number') or ''} for x in rows]}
            cache['sp:' + mid] = ent
        except RuntimeError:
            budget_out = True
    if not ent: continue
    m = {}
    for d in ent['donors']:
        k = (d['name'] or '').strip().upper()
        a = m.setdefault(k, {'name': d['name'], 'employer': d['employer'], 'occupation': d['occupation'], 'amount': 0.0, 'n': 0, 'type': d['type'], 'image': d['image'], 'date': d['date']})
        a['amount'] += d['amount']; a['n'] += 1
    donors = sorted(m.values(), key=lambda a: -a['amount'])[:40]
    for d in donors:
        d['amount'] = round(d['amount'], 2); d['ind'] = classify_employer(d['employer'] if d['type'] == 'IND' else d['name'])
    sp[mid] = {'name': cm['name'], 'class': cm.get('class'), 'donors': donors, 'fetched': ent['fetched']}

# ---- nominees: committees linked by the FEC, then receipts by employer for each
for c, r in nominees:
    cid = c['id']
    ent = cache.get('cand:' + cid)
    if not fresh(ent) and not budget_out:
        try:
            cmtes = paged(f'/candidate/{cid}/committees/', cycle=A.cycle, per_page=100)
            cmtes = [x for x in cmtes if x.get('designation') in ('P', 'A', 'D', 'J')]
            rec = {'fetched': now.isoformat(), 'committees': [], 'employers': {}}
            for cm in cmtes:
                mid = cm['committee_id']
                rec['committees'].append({'id': mid, 'name': cm.get('name'), 'dsgn': cm.get('designation'), 'type': cm.get('committee_type')})
                rows = paged('/schedules/schedule_a/by_employer/', limit_pages=1, committee_id=mid, cycle=A.cycle, sort='-total', per_page=100)
                rec['employers'][mid] = [{'e': x.get('employer') or '', 't': round(x.get('total') or 0, 2), 'n': x.get('count') or 0} for x in rows]
            cache['cand:' + cid] = ent = rec
            done += 1
        except RuntimeError:
            budget_out = True
            if not ent: continue
            stale += 1
    elif ent:
        skipped += 1
    else:
        continue
    agg = {}
    for mid, rows in ent['employers'].items():
        dsgn = next((x['dsgn'] for x in ent['committees'] if x['id'] == mid), '?')
        for x in rows:
            k = (x['e'] or '').strip().upper()
            a = agg.setdefault(k, {'e': x['e'], 't': 0.0, 'n': 0, 'via': set(), 'ind': classify_employer(x['e'])})
            a['t'] += x['t']; a['n'] += x['n']; a['via'].add(dsgn)
    emp = sorted(agg.values(), key=lambda a: -a['t'])
    by_ind = {}
    for a in emp:
        by_ind[a['ind']] = round(by_ind.get(a['ind'], 0.0) + a['t'], 2)
    out_cands[cid] = {'name': c['name'], 'party': c['party'], 'race': r['id'], 'committees': ent['committees'],
                      'employers': [{'e': a['e'], 't': round(a['t'], 2), 'n': a['n'], 'ind': a['ind'], 'via': sorted(a['via'])} for a in emp[:150]],
                      'by_industry': dict(sorted(by_ind.items(), key=lambda kv: -kv[1])), 'itemized_total': round(sum(a['t'] for a in emp), 2),
                      'fetched': ent['fetched']}
if budget_out:
    print(f'call budget reached after {calls} calls; {len(nominees) - done - skipped - stale} nominees not yet covered, next run continues')

if A.cache:
    json.dump(cache, open(A.cache, 'w'), separators=(',', ':'))
meta = {'built_at': now.strftime('%Y-%m-%dT%H:%MZ'), 'cycle': A.cycle, 'api_calls': calls, 'candidates_refreshed': done, 'candidates_from_cache': skipped, 'candidates_stale': stale, 'nominees_total': len(nominees),
        'n_candidates': len(out_cands), 'labels': EMP_LABELS,
        'note': 'Itemized individual contributions only (donors over $200 in a cycle), aggregated by the employer the donor reported, via the FEC API. Includes the candidate\'s principal, authorized, leadership and joint fundraising committees as linked by the FEC.'}
json.dump({'meta': meta, 'candidates': out_cands, 'superpacs': sp}, open(A.out, 'w'), separators=(',', ':'))
print(json.dumps(meta, indent=1))
