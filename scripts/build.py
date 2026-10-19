#!/usr/bin/env python3
"""Build site/data/*.json and site/photos/ from upstream sources.

Inputs (all optional except --upstream):
  --upstream DIR     checkout of sabbadoo32/follow-the-money-2026 (uses site/data/{meta,races}.json, expenditures.csv)
  --legislators F    legislators-current.json from unitedstates/congress-legislators
  --weball F         FEC all-candidates summary (weball26.txt, pipe-delimited) — adds PAC / self-funding split
                     for every nominee and adds races that have no outside spending yet
  --cn F             FEC candidate master (cn26.txt) — needed with --weball to add the extra races
  --factchecks F     hand-curated data/factchecks.json (copied through)
  --out DIR          site directory (default: site)
Standard library + Pillow only.
"""
import argparse, json, csv, collections, re, os, subprocess, io, zipfile
from datetime import date, datetime, timezone

ap = argparse.ArgumentParser()
ap.add_argument('--upstream', required=True)
ap.add_argument('--legislators')
ap.add_argument('--weball')
ap.add_argument('--cn')
ap.add_argument('--factchecks')
ap.add_argument('--out', default='site')
ap.add_argument('--no-photos', action='store_true')
A = ap.parse_args()

FTM = os.path.join(A.upstream, 'site', 'data')
OUT = A.out
os.makedirs(os.path.join(OUT, 'data'), exist_ok=True)
os.makedirs(os.path.join(OUT, 'photos'), exist_ok=True)

meta = json.load(open(os.path.join(FTM, 'meta.json')))
races = json.load(open(os.path.join(FTM, 'races.json')))['races']
rows = list(csv.DictReader(open(os.path.join(FTM, 'expenditures.csv'))))
legs = json.load(open(A.legislators)) if A.legislators and os.path.exists(A.legislators) else []

# ---------------- FEC bulk: all-candidates summary + candidate master
def read_pipe(path):
    """Accepts a .txt or a .zip holding one .txt."""
    if path.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.lower().endswith('.txt')][0]
            return io.TextIOWrapper(z.open(name), encoding='latin-1').read().splitlines()
    return open(path, encoding='latin-1').read().splitlines()

def money(v):
    try: return round(float(v or 0), 2)
    except ValueError: return 0.0

def fec_date(s):
    s = (s or '').strip()
    m = re.fullmatch(r'(\d{2})/(\d{2})/(\d{4})', s)
    return f'{m.group(3)}-{m.group(1)}-{m.group(2)}' if m else None

WEBALL = {}
if A.weball and os.path.exists(A.weball):
    for line in read_pipe(A.weball):
        f = line.split('|')
        if len(f) < 30: continue
        WEBALL[f[0]] = {
            'name': f[1], 'ici': f[2], 'party': f[4], 'receipts': money(f[5]), 'trans_from_auth': money(f[6]),
            'disb': money(f[7]), 'coh': money(f[10]), 'cand_contrib': money(f[11]), 'cand_loans': money(f[12]),
            'other_loans': money(f[13]), 'debts': money(f[16]), 'indiv': money(f[17]), 'state': f[18], 'district': f[19],
            'pac': money(f[25]), 'party_money': money(f[26]), 'through': fec_date(f[27]), 'indiv_refunds': money(f[28]), 'cmte_refunds': money(f[29]),
        }
CN = {}
if A.cn and os.path.exists(A.cn):
    for line in read_pipe(A.cn):
        f = line.split('|')
        if len(f) < 10: continue
        CN[f[0]] = {'name': f[1], 'party': f[2], 'yr': f[3], 'state': f[4], 'office': f[5], 'district': (f[6] or '00').zfill(2)[-2:], 'ici': f[7], 'status': f[8], 'pcc': f[9]}

# ---------------- legislators
fec2leg = {}
for l in legs:
    for f in l['id'].get('fec', []):
        fec2leg[f] = l

def norm(s): return re.sub(r'[^a-z]', '', (s or '').lower())

def leg_match_holder(r):
    hn = r.get('holder_name') or ''
    if not hn or not legs: return None
    office = 'sen' if r['office'] == 'S' else 'rep'
    cands = []
    for l in legs:
        t = l['terms'][-1]
        if t['state'] != r['state'] or t['type'] != office: continue
        if office == 'rep' and r['district'] not in (None, '', '00') and str(t.get('district')).zfill(2) != str(r['district']).zfill(2): continue
        cands.append(l)
    hn_n = norm(hn)
    for l in cands:
        if norm(l['name']['last']) in hn_n: return l
    if ',' in hn:
        last = norm(hn.split(',')[0])
        for l in cands:
            if norm(l['name']['last']) == last or last in norm(l['name']['official_full']): return l
    return None

def seat_holder(office, state, district):
    """For races absent upstream: current member for that seat."""
    t_type = 'sen' if office == 'S' else 'rep'
    out = []
    for l in legs:
        t = l['terms'][-1]
        if t['state'] == state and t['type'] == t_type and (office == 'S' or str(t.get('district')).zfill(2) == district):
            out.append(l)
    return out

# ---------------- medium classification
MEDIA_RULES = [(k, re.compile(p)) for k, p in [
    ('tv', r'\b(TV|TELEVISION|BROADCAST|CABLE|CTV|CONNECTED TV|OTT|STREAMING|VIDEO AD|RADIO|AUDIO)\b'),
    ('digital', r'\b(DIGITAL|ONLINE|SOCIAL|FACEBOOK|META|GOOGLE|YOUTUBE|WEB|INTERNET|PROGRAMMATIC|SEARCH ADS|BANNER)\b'),
    ('mail', r'\b(MAIL|MAILER|MAILERS|POSTAGE|PRINTING|PRINT|LITERATURE|FLYER|DOOR HANGER|DOORHANGER)\b'),
    ('phone', r'\b(TEXT|TEXTS|TEXTING|SMS|PHONE|CALLS|CALL|ROBOCALL|TELEPHONE|P2P)\b'),
    ('field', r'\b(CANVASS|CANVASSING|CANVASSERS|FIELD|GOTV|DOOR|ORGANIZING|VOTER CONTACT|RALLY|EVENT|SIGNS|YARD)\b'),
    ('production', r'\b(PRODUCTION|PRODUCE|CREATIVE|EDITING|FILMING|VOICEOVER)\b'),
    ('media_buy', r'\b(MEDIA|AD BUY|ADVERTISING|ADS|AD PLACEMENT|PLACEMENT|ADVERTISEMENT|BILLBOARD|OUTDOOR|AIRTIME)\b'),
    ('polling', r'\b(POLL|POLLING|SURVEY|RESEARCH)\b'),
    ('staff', r'\b(SALARY|SALARIES|PAYROLL|STAFF|CONSULTING|CONSULTANT|LEGAL|TRAVEL|MILEAGE)\b'),
]]
def medium(purpose):
    p = (purpose or '').upper()
    for k, rx in MEDIA_RULES:
        if rx.search(p): return k
    return 'other'

# ---------------- items
cm_names, payees, purposes = {}, {}, {}
def idx(table, v):
    v = v or ''
    if v not in table: table[v] = len(table)
    return table[v]
def inv(table):
    out = [''] * len(table)
    for k, v in table.items(): out[v] = k
    return out

race_index = {r['race_id']: i for i, r in enumerate(races)}
items = []
by_race_party = collections.defaultdict(float)
by_cand = collections.defaultdict(lambda: {'for': 0.0, 'against': 0.0, 'n_for': 0, 'n_against': 0})
by_cand_cmte = collections.defaultdict(float)
by_cand_medium = collections.defaultdict(float)
by_cand_week = collections.defaultdict(float)
estimates = 0
for r in rows:
    rid = r['race_id']
    if rid not in race_index: continue
    amt = money(r['amount']); so = r['support_oppose']; med = medium(r['purpose'])
    if 'ESTIMAT' in (r['purpose'] or '').upper(): estimates += 1
    d = r['dissemination_date'] or r['filed_at']
    items.append([race_index[rid], r['candidate_id'], r['committee_id'], idx(cm_names, r['committee_name']), r['committee_class'], so, r['helps'], amt, d, r['kind'],
                  idx(payees, r['payee']), idx(purposes, r['purpose']), r['image_number'], r['source'], med, r['filed_at'], r['file_num']])
    by_race_party[(rid, r['helps'])] += amt
    c = by_cand[r['candidate_id']]; key = 'for' if so == 'S' else 'against'; c[key] += amt; c['n_' + key] += 1
    by_cand_cmte[(r['candidate_id'], so, r['committee_id'])] += amt
    by_cand_medium[(r['candidate_id'], so, med)] += amt
    if d:
        try:
            wk = date.fromisoformat(d[:10]).isocalendar(); by_cand_week[(r['candidate_id'], so, f'{wk[0]}-W{wk[1]:02d}')] += amt
        except ValueError: pass

# ---------------- photos
photo_ok = {}
def fetch_photo(bioguide):
    dst = os.path.join(OUT, 'photos', bioguide + '.jpg')
    if os.path.exists(dst): return True
    if A.no_photos: return False
    url = f'https://raw.githubusercontent.com/unitedstates/images/gh-pages/congress/225x275/{bioguide}.jpg'
    tmp = dst + '.tmp'
    rc = subprocess.run(['curl', '-sS', '--max-time', '30', '-f', '-o', tmp, url], capture_output=True)
    if rc.returncode != 0 or not os.path.exists(tmp): return False
    try:
        from PIL import Image
        im = Image.open(tmp).convert('RGB'); im.thumbnail((160, 196)); im.save(dst, 'JPEG', quality=74, optimize=True, progressive=True)
        os.remove(tmp); return True
    except Exception as e:
        print('photo fail', bioguide, e)
        if os.path.exists(tmp): os.remove(tmp)
        return False
def photo_for(leg):
    if not leg: return None
    b = leg['id']['bioguide']
    if b not in photo_ok: photo_ok[b] = fetch_photo(b)
    return b if photo_ok[b] else None

# ---------------- names
def fmt_name(n):
    if not n: return ''
    if ',' not in n: return n.title() if n.isupper() else n
    last, rest = n.split(',', 1)
    rest = re.sub(r'\b(JR\.?|SR\.?|II|III|IV|MR\.?|MRS\.?|MS\.?|DR\.?|HON\.?|M\.C\.)\b', '', rest.strip()).strip()
    parts = rest.split()
    if len(parts) > 1 and re.fullmatch(r'[A-Z]\.?', parts[0]): parts = parts[1:]
    first = parts[0] if parts else ''
    def cap(w):
        w = w.lower(); w = '-'.join(p.capitalize() for p in w.split('-')); w = "'".join(p.capitalize() for p in w.split("'"))
        if w.startswith('Mc') and len(w) > 2: w = 'Mc' + w[2:].capitalize()
        return w
    return (cap(first) + ' ' + ' '.join(cap(w) for w in last.strip().split())).strip()

def money_block(c_up, wb):
    """Candidate money record. c_up = upstream cand dict (may be None); wb = weball row (may be None)."""
    if wb:
        self_fund = round(wb['cand_contrib'] + wb['cand_loans'], 2)
        residual = round(wb['receipts'] - wb['indiv'] - wb['party_money'] - wb['pac'] - self_fund - wb['other_loans'] - wb['trans_from_auth'], 2)
        m = {'receipts': wb['receipts'], 'coh': wb['coh'], 'indiv': wb['indiv'], 'party': wb['party_money'], 'pac': wb['pac'], 'self': self_fund,
             'other_loans': wb['other_loans'], 'transfers': wb['trans_from_auth'], 'residual': residual, 'debts': wb['debts'], 'disb': wb['disb'],
             'other': round(wb['receipts'] - wb['indiv'] - wb['party_money'], 2), 'through': wb['through'], 'itemized': True}
    elif c_up:
        receipts = c_up.get('receipts') or 0.0; indiv = c_up.get('indiv') or 0.0; party = c_up.get('from_party') or 0.0
        m = {'receipts': receipts, 'coh': c_up.get('coh'), 'indiv': indiv, 'party': party, 'other': round(receipts - indiv - party, 2), 'through': c_up.get('through'), 'itemized': False}
    else:
        return None
    if c_up:
        m.update({'late_48h': c_up.get('late_48h'), 'late_48h_count': c_up.get('late_48h_count'), 'late_48h_through': c_up.get('late_48h_through')})
    return m

# ---------------- races from upstream
out_races = []
checks_total = {'race_total_match': 0, 'race_total_mismatch': 0, 'cand_receipts_ok': 0, 'cand_receipts_flag': 0, 'itemized_ok': 0, 'itemized_flag': 0}
for r in races:
    rid = r['race_id']
    rec = {'id': rid, 'office': r['office'], 'state': r['state'], 'district': r['district'], 'holder': r.get('holder_name'), 'holder_party': r['holder_party'],
           'holder_uncertain': r['holder_uncertain'], 'uncertain_reason': r.get('uncertain_reason'), 'open': r['open_seat'], 'total': r['total'], 'last_7d': r['last_7d'],
           'note': r.get('note'), 'split': r.get('split_signal'), 'sides': {}, 'cands': [], 'checks': [], 'has_ie': True}
    hl = leg_match_holder(r); rec['holder_photo'] = photo_for(hl)
    if hl: rec['holder_bioguide'] = hl['id']['bioguide']
    n_items_race = sum(1 for it in items if it[0] == race_index[rid])
    for p in ('DEM', 'REP'):
        s = r[p]; recomputed = round(by_race_party.get((rid, p), 0.0), 2)
        rec['sides'][p] = {'party_ie': s['party_ie'], 'party_coord': s['party_coord'], 'outside': s['outside'], 'super_pac': s['super_pac'], 'total': s['total'],
                           'last_7d': s['last_7d'], 'side': s['side'], 'first_party_dollar': s['first_party_dollar'], 'recomputed': recomputed}
        diff = round(recomputed - s['total'], 2); ok = abs(diff) < 1.0
        checks_total['race_total_match' if ok else 'race_total_mismatch'] += 1
        rec['checks'].append({'id': f'sum-{p}', 'ok': ok, 'label': f'{"Pro-Democratic" if p == "DEM" else "Pro-Republican"} outside total re-added from {sum(1 for it in items if it[0] == race_index[rid] and it[6] == p):,} line items',
                              'detail': f'Published ${s["total"]:,.2f}; re-added ${recomputed:,.2f}; difference ${diff:,.2f}'})
    cand_lookup = {}
    for c in r['candidates']:
        cand_lookup[c['candidate_id']] = {'id': c['candidate_id'], 'name': fmt_name(c['name']), 'fec_name': c['name'], 'party': c['party']}
    for p in ('DEM', 'REP'):
        c = (r.get('cand') or {}).get(p)
        if not c: continue
        cid = c['candidate_id']
        rc = cand_lookup.setdefault(cid, {'id': cid, 'name': fmt_name(c['name']), 'fec_name': c['name'], 'party': p})
        rc.update({'nominee': True, 'basis': c.get('basis'), 'committee_id': c.get('committee_id'), 'reported': c.get('reported')})
        m = money_block(c, WEBALL.get(cid)); rc['money'] = m
        if m:
            ok = m['other'] >= -1.0 and m['receipts'] >= 0
            checks_total['cand_receipts_ok' if ok else 'cand_receipts_flag'] += 1
            rec['checks'].append({'id': f'cand-{p}', 'ok': ok, 'label': f'{rc["name"]}: itemized sources do not exceed total receipts',
                                  'detail': f'Receipts ${m["receipts"]:,.2f} = individuals ${m["indiv"]:,.2f} + party ${m["party"]:,.2f} + other sources ${m["other"]:,.2f}'})
            if m.get('itemized'):
                ok2 = abs(m['residual']) <= max(1.0, 0.05 * m['receipts'])
                checks_total['itemized_ok' if ok2 else 'itemized_flag'] += 1
                rec['checks'].append({'id': f'item-{p}', 'ok': ok2, 'label': f'{rc["name"]}: PAC, self-funding, loan and transfer lines add up to receipts',
                                      'detail': f'PACs ${m["pac"]:,.2f} + self ${m["self"]:,.2f} + other loans ${m["other_loans"]:,.2f} + transfers ${m["transfers"]:,.2f}; unexplained remainder ${m["residual"]:,.2f} (refunds and offsets)'})
    for cid, rc in cand_lookup.items():
        leg = fec2leg.get(cid); rc['photo'] = photo_for(leg)
        if leg: rc['bioguide'] = leg['id']['bioguide']; rc['incumbent_member'] = True
        agg = by_cand.get(cid)
        rc['ie'] = {'for': round(agg['for'], 2), 'against': round(agg['against'], 2), 'n_for': agg['n_for'], 'n_against': agg['n_against']} if agg else {'for': 0, 'against': 0, 'n_for': 0, 'n_against': 0}
        meds = {}
        for (c2, so, med), amt in by_cand_medium.items():
            if c2 == cid: meds.setdefault(so, {})[med] = round(amt, 2)
        rc['mediums'] = meds
        cm = collections.defaultdict(lambda: {'S': 0.0, 'O': 0.0})
        for (c2, so, cmid), amt in by_cand_cmte.items():
            if c2 == cid: cm[cmid][so] += amt
        rc['committees'] = sorted(({'id': k, 'S': round(v['S'], 2), 'O': round(v['O'], 2)} for k, v in cm.items()), key=lambda x: -(x['S'] + x['O']))[:25]
        wk = collections.defaultdict(lambda: {'S': 0.0, 'O': 0.0})
        for (c2, so, w), amt in by_cand_week.items():
            if c2 == cid: wk[w][so] += amt
        rc['weeks'] = [{'w': w, 'S': round(v['S'], 2), 'O': round(v['O'], 2)} for w, v in sorted(wk.items())]
    rec['cands'] = sorted(cand_lookup.values(), key=lambda c: (c['party'] != 'DEM', not c.get('nominee', False), c['name']))
    rec['top_spenders'] = r.get('top_spenders', [])[:10]
    out_races.append(rec)

# ---------------- races with no outside spending (needs weball + cn + legislators)
added = 0
if WEBALL and CN:
    have = {r['id'] for r in out_races}
    seats = collections.defaultdict(list)
    for cid, c in CN.items():
        if c['yr'] != '2026' or c['office'] not in ('H', 'S') or c['party'] not in ('DEM', 'REP'): continue
        wb = WEBALL.get(cid)
        if not wb or wb['receipts'] <= 0: continue
        rid = f"S-{c['state']}" if c['office'] == 'S' else f"H-{c['state']}-{c['district']}"
        if rid in have: continue
        seats[rid].append((cid, c, wb))
    for rid, lst in seats.items():
        office, state = rid.split('-')[0], rid.split('-')[1]
        district = rid.split('-')[2] if office == 'H' else None
        holders = seat_holder(office, state, district)
        # Senate: several sitting senators per state; pick the one up in 2026 (class 2) when known
        if office == 'S':
            holders = [h for h in holders if h['terms'][-1].get('class') == 2] or holders
        hl = holders[0] if holders else None
        holder_party = {'Democrat': 'DEM', 'Republican': 'REP'}.get(hl['terms'][-1]['party'], 'OTH') if hl else None
        rec = {'id': rid, 'office': office, 'state': state, 'district': district, 'holder': hl['name']['official_full'] if hl else None, 'holder_party': holder_party,
               'holder_uncertain': False, 'uncertain_reason': None, 'open': bool(hl) and not any(cid in fec2leg and fec2leg[cid] is hl for cid, _, _ in lst),
               'total': 0.0, 'last_7d': 0.0, 'note': 'No outside spending reported in this race yet. Nominees are the top fundraiser of each party and may be primary candidates.',
               'split': None, 'sides': {p: {'party_ie': 0, 'party_coord': 0, 'outside': 0, 'super_pac': 0, 'total': 0, 'last_7d': 0, 'side': None, 'first_party_dollar': None, 'recomputed': 0} for p in ('DEM', 'REP')},
               'cands': [], 'checks': [], 'has_ie': False, 'holder_photo': photo_for(hl)}
        for p in ('DEM', 'REP'):
            best = sorted([x for x in lst if x[1]['party'] == p], key=lambda x: -x[2]['receipts'])[:1]
            for cid, c, wb in best:
                leg = fec2leg.get(cid)
                m = money_block(None, wb)
                rec['cands'].append({'id': cid, 'name': fmt_name(c['name']), 'fec_name': c['name'], 'party': p, 'nominee': True, 'basis': 'receipts', 'committee_id': c['pcc'],
                                     'money': m, 'photo': photo_for(leg), 'bioguide': leg['id']['bioguide'] if leg else None, 'incumbent_member': bool(leg),
                                     'ie': {'for': 0, 'against': 0, 'n_for': 0, 'n_against': 0}, 'mediums': {}, 'committees': [], 'weeks': []})
        rec['top_spenders'] = []
        out_races.append(rec); added += 1

# ---------------- national
cm_name_by_id, cm_class_by_id = {}, {}
for r in rows:
    cm_name_by_id.setdefault(r['committee_id'], r['committee_name']); cm_class_by_id.setdefault(r['committee_id'], r['committee_class'])
nat = {'generated_at': meta['generated_at'], 'bulk_last_modified': meta['bulk_last_modified'], 'coord_through': meta['coord_through'], 'coord_banner': meta['coord_banner'],
       'headline': meta['headline'], 'cand_headline': meta.get('cand_headline'), 'n_races': len(races), 'n_races_total': len(out_races), 'n_items': len(items), 'n_estimates': estimates,
       'checks': checks_total, 'built_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), 'itemized': bool(WEBALL), 'weball_candidates': len(WEBALL)}
nat_cm = collections.defaultdict(lambda: {'DEM': 0.0, 'REP': 0.0, 'n': 0})
for it in items: nat_cm[it[2]][it[6]] += it[7]; nat_cm[it[2]]['n'] += 1
nat['top_committees'] = sorted(({'id': k, 'name': cm_name_by_id.get(k, k), 'class': cm_class_by_id.get(k, ''), 'DEM': round(v['DEM'], 2), 'REP': round(v['REP'], 2), 'n': v['n']} for k, v in nat_cm.items()), key=lambda x: -(x['DEM'] + x['REP']))[:40]
nat_med = collections.defaultdict(float)
for it in items: nat_med[it[14]] += it[7]
nat['medium_mix'] = {k: round(v, 2) for k, v in nat_med.items()}
nat_wk = collections.defaultdict(lambda: {'DEM': 0.0, 'REP': 0.0})
for it in items:
    if it[8]:
        try:
            wk = date.fromisoformat(it[8][:10]).isocalendar(); nat_wk[f'{wk[0]}-W{wk[1]:02d}'][it[6]] += it[7]
        except ValueError: pass
nat['weekly'] = [{'w': w, 'DEM': round(v['DEM'], 2), 'REP': round(v['REP'], 2)} for w, v in sorted(nat_wk.items()) if w >= '2025-W01']

json.dump({'meta': nat, 'races': out_races, 'committees': {k: {'name': v, 'class': cm_class_by_id.get(k, '')} for k, v in cm_name_by_id.items()}},
          open(os.path.join(OUT, 'data', 'races.json'), 'w'), separators=(',', ':'))
json.dump({'cols': ['race', 'cand', 'cmte', 'cmte_name', 'class', 'so', 'helps', 'amount', 'date', 'kind', 'payee', 'purpose', 'image', 'source', 'medium', 'filed', 'file_num'],
           'cmte_names': inv(cm_names), 'payees': inv(payees), 'purposes': inv(purposes), 'items': items}, open(os.path.join(OUT, 'data', 'items.json'), 'w'), separators=(',', ':'))
if A.factchecks and os.path.exists(A.factchecks):
    fc = json.load(open(A.factchecks)); json.dump(fc, open(os.path.join(OUT, 'data', 'factchecks.json'), 'w'), separators=(',', ':'))
print(f'races {len(races)} (+{added} without outside spending) items {len(items)} checks {checks_total} photos {sum(1 for v in photo_ok.values() if v)}/{len(photo_ok)} weball {len(WEBALL)}')
if checks_total['race_total_mismatch'] or checks_total['cand_receipts_flag']:
    raise SystemExit('verification failed: ' + json.dumps(checks_total))
