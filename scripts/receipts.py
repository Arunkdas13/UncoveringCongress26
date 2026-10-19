#!/usr/bin/env python3
"""Industry receipts: PAC-to-candidate contributions (FEC pas2 file) tagged by industry.

  --pas2 FILE      pas226.zip or itpas2.txt  (contributions from committees to candidates)
  --cm FILE        cm26.zip or cm.txt        (committee master, with connected organizations)
  --races FILE     site/data/races.json      (to know which candidates are on the site)
  --out FILE       site/data/receipts.json

Only transaction type 24K (a committee's contribution to a candidate) is counted. Independent
expenditures (24A/24E) in the same file are ignored here; the site gets them from Schedule E.
Amended filings replace originals (latest file number per committee + transaction id).
"""
import argparse, json, re, io, zipfile, collections, os

ap = argparse.ArgumentParser()
ap.add_argument('--pas2', required=True); ap.add_argument('--cm', required=True)
ap.add_argument('--races', required=True); ap.add_argument('--out', required=True)
A = ap.parse_args()

def read_pipe(path):
    if path.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.lower().endswith('.txt')][0]
            return io.TextIOWrapper(z.open(name), encoding='latin-1').read().splitlines()
    return open(path, encoding='latin-1').read().splitlines()

# ---------------- committee master
CM = {}
for line in read_pipe(A.cm):
    f = line.split('|')
    if len(f) < 15: continue
    CM[f[0]] = {'name': f[1].strip(), 'dsgn': f[8], 'tp': f[9], 'party': f[10], 'org': f[12], 'conn': f[13].strip(), 'cand': f[14]}

from industries import RULES, LABELS, classify

# ---------------- contributions
races = json.load(open(A.races))
site_cands = {}
for r in races['races']:
    for c in r['cands']:
        site_cands[c['id']] = {'name': c['name'], 'party': c['party'], 'race': r['id'], 'nominee': c.get('nominee', False),
                               'receipts': (c.get('money') or {}).get('receipts'), 'other': (c.get('money') or {}).get('other'), 'pac_summary': (c.get('money') or {}).get('pac'), 'through': (c.get('money') or {}).get('through')}

latest = {}   # (cmte, tran_id) -> row with max file_num   (amendments replace originals)
n_raw = n_dupe = 0
for line in read_pipe(A.pas2):
    f = line.split('|')
    if len(f) < 22 or f[5] != '24K': continue
    cand = f[16]
    if not cand or cand[0] not in 'HS': continue
    n_raw += 1
    key = (f[0], f[17] or f[21])
    try: fn = int(f[18] or 0)
    except ValueError: fn = 0
    if key in latest:
        n_dupe += 1
        if fn < latest[key][0]: continue
    latest[key] = (fn, f)

def fdate(s):
    s = (s or '').strip()
    return f'{s[4:8]}-{s[0:2]}-{s[2:4]}' if len(s) == 8 else None

by_cand = collections.defaultdict(lambda: {'total': 0.0, 'n': 0, 'ind': collections.defaultdict(float)})
items = collections.defaultdict(list)
ind_total = collections.defaultdict(float)
cmte_total = collections.defaultdict(float)
cmte_class = {}
for fn, f in latest.values():
    cid, cand = f[0], f[16]
    amt = round(float(f[14] or 0), 2)
    if amt <= 0: continue   # refunds/negative entries are netted via the summary file, not here
    cm = CM.get(cid, {'name': f[7], 'dsgn': '', 'tp': '', 'party': '', 'org': '', 'conn': '', 'cand': ''})
    k = cmte_class.get(cid)
    if k is None: k = cmte_class[cid] = classify(cm)
    c = by_cand[cand]; c['total'] += amt; c['n'] += 1; c['ind'][k] += amt
    ind_total[k] += amt; cmte_total[cid] += amt
    if cand in site_cands:
        items[cand].append([cid, amt, fdate(f[13]), f[4], f[18], k])

# ranks per industry across every House/Senate candidate in the file
rank = {}
for k in LABELS:
    ordered = sorted(((v['ind'].get(k, 0.0), cand) for cand, v in by_cand.items() if v['ind'].get(k, 0.0) > 0), reverse=True)
    rank[k] = {cand: i + 1 for i, (_, cand) in enumerate(ordered)}
    rank[k]['_n'] = len(ordered)
rank_total = {cand: i + 1 for i, (_, cand) in enumerate(sorted(((v['total'], cand) for cand, v in by_cand.items()), reverse=True))}

# names for out-of-site candidates come from their principal committee in the committee master
cand_name = {}
for cid, cm in CM.items():
    if cm['tp'] in ('H', 'S') and cm['cand'] and cm['dsgn'] == 'P':
        cand_name.setdefault(cm['cand'], cm['name'].title())
for cid, s in site_cands.items(): cand_name[cid] = s['name']

# top recipients per industry (national leaderboard)
leaders = {}
for k in LABELS:
    top = sorted(((v['ind'].get(k, 0.0), cand) for cand, v in by_cand.items() if v['ind'].get(k, 0.0) > 0), reverse=True)[:15]
    leaders[k] = [{'cand': cand, 'name': cand_name.get(cand, cand), 'amount': round(a, 2), 'site': cand in site_cands, 'party': site_cands.get(cand, {}).get('party'), 'race': site_cands.get(cand, {}).get('race')} for a, cand in top]

# candidate records for the site
out_cands = {}
checks = {'pac_within_other_ok': 0, 'pac_within_other_flag': 0, 'pac_vs_summary_ok': 0, 'pac_vs_summary_flag': 0}
for cand, s in site_cands.items():
    v = by_cand.get(cand)
    rec = {'name': s['name'], 'party': s['party'], 'race': s['race'], 'total': round(v['total'], 2) if v else 0.0, 'n': v['n'] if v else 0,
           'industries': {k: round(a, 2) for k, a in sorted((v['ind'].items() if v else []), key=lambda x: -x[1])},
           'rank_total': rank_total.get(cand), 'n_ranked': len(rank_total),
           'ranks': {k: rank[k].get(cand) for k in LABELS if v and v['ind'].get(k)},
           'receipts': s['receipts'], 'share': (round(v['total'] / s['receipts'], 4) if v and s['receipts'] else None),
           'items': sorted(items.get(cand, []), key=lambda x: -x[1])[:400], 'checks': []}
    thru = s.get('through') or '9999-12-31'
    in_period = round(sum(x[1] for x in items.get(cand, []) if (x[2] or '') <= thru), 2)
    rec['total_through_summary'] = in_period
    if v and s['other'] is not None and s['nominee']:
        ok = in_period <= s['other'] + max(5000.0, 0.05 * in_period)
        checks['pac_within_other_ok' if ok else 'pac_within_other_flag'] += 1
        rec['checks'].append({'ok': ok, 'label': f'PAC checks dated through {thru} fit inside the non-individual, non-party receipts in the FEC summary',
                              'detail': f'PAC contributions re-added here ${in_period:,.2f}; summary file leaves ${s["other"]:,.2f} for PACs, self-funding, loans and transfers'})
    if v and s['pac_summary'] is not None:
        ok = abs(in_period - s['pac_summary']) <= max(5000.0, 0.1 * s['pac_summary'])
        checks['pac_vs_summary_ok' if ok else 'pac_vs_summary_flag'] += 1
        rec['checks'].append({'ok': ok, 'label': f'Itemized PAC checks through {thru} match the FEC summary total for committee contributions',
                              'detail': f'Itemized ${in_period:,.2f} vs summary ${s["pac_summary"]:,.2f}'})
    out_cands[cand] = rec

committees = {cid: {'name': CM[cid]['name'] if cid in CM else cid, 'conn': CM.get(cid, {}).get('conn', ''), 'class': cmte_class.get(cid, 'other'),
                    'org': CM.get(cid, {}).get('org', ''), 'tp': CM.get(cid, {}).get('tp', ''), 'total': round(cmte_total[cid], 2)}
              for cid in set(x[0] for lst in items.values() for x in lst)}
top_committees = {k: [{'id': cid, 'name': CM[cid]['name'], 'conn': CM[cid]['conn'], 'total': round(t, 2)} for cid, t in sorted(((c, t) for c, t in cmte_total.items() if cmte_class.get(c) == k), key=lambda x: -x[1])[:12]] for k in LABELS}

out = {'meta': {'n_rows_24k': n_raw, 'n_superseded': n_dupe, 'n_contributions': sum(v['n'] for v in by_cand.values()), 'total': round(sum(v['total'] for v in by_cand.values()), 2),
                'n_candidates': len(by_cand), 'n_site_candidates': sum(1 for c in out_cands.values() if c['n']), 'checks': checks,
                'industry_totals': {k: round(ind_total.get(k, 0.0), 2) for k in LABELS}, 'industry_n': {k: rank[k]['_n'] for k in LABELS}, 'labels': LABELS,
                'unclassified_share': round(sum(ind_total.get(k, 0.0) for k in ('other', 'other_corp', 'other_trade', 'other_member')) / max(1.0, sum(ind_total.values())), 4)},
       'candidates': out_cands, 'committees': committees, 'leaders': leaders, 'top_committees': top_committees}
os.makedirs(os.path.dirname(A.out), exist_ok=True)
json.dump(out, open(A.out, 'w'), separators=(',', ':'))
print(json.dumps(out['meta'], indent=1))
print('size KB', os.path.getsize(A.out) // 1024)
