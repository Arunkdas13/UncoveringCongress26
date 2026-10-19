# Follow the Ads 2026

Outside money, the ads it bought, and published fact-checks for every 2026 U.S. House and Senate race, built from FEC filings. A static site: one HTML page plus JSON data and member photos. No server, no database, no API keys.

## What's in it

- **Races** — every race with FEC-reported independent expenditures (and, when the FEC bulk files are available at build time, every other race too), with pro-Democratic / pro-Republican totals, seat holder, nominees and a verification panel.
- **Candidate money** — each nominee's receipts split into individuals, PACs, party, self-funding and transfers (from the FEC all-candidates summary), cash on hand and 48-hour large-contribution notices.
- **Outside money for and against** each candidate, by week, by medium (TV/streaming, digital, mail, texts, production, field…) and by committee, with every line item linked to its FEC filing image.
- **Who's paying** — top 40 committees, party lean, offense/defense share.
- **Ad fact checks** — hand-curated `data/factchecks.json`: published checks from PolitiFact, FactCheck.org, CNN, Snopes and local newsrooms, with the outlet's own rating and a link.
- **Methods & checks** — sources, inclusion rules, and the automated reconciliation results for the current build.

## Layout

```
site/            the deployable site (index.html, data/, photos/)
scripts/fetch.sh pulls the upstream inputs into work/
scripts/build.py turns them into site/data/*.json and site/photos/*.jpg
data/factchecks.json   curated fact-check list (edit by hand)
.github/workflows/build.yml   daily rebuild + GitHub Pages deploy
```

## Data sources

| Layer | Source |
|---|---|
| Independent expenditures (Schedule E), party coordinated spending (Schedule F), nominee identification, dedupe | [sabbadoo32/follow-the-money-2026](https://github.com/sabbadoo32/follow-the-money-2026), which rebuilds from FEC bulk files hourly |
| Candidate receipts itemized (individuals, PACs, party, self-funding, transfers) | FEC bulk `weball26.zip` (all-candidates summary) and `cn26.zip` (candidate master) |
| Seat holders and photos | [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators), [unitedstates/images](https://github.com/unitedstates/images) |
| Fact checks | the outlets linked on each item |

## Verification the build performs

- Each race's published pro-Dem and pro-Rep totals are re-added from the individual line items and must match within $1; a mismatch fails the build.
- For every nominee, individuals + party money must not exceed total receipts; with the FEC summary present, PAC + self-funding + loans + transfers must add up to receipts within 5%.
- Every expenditure carries an FEC image number or file number so it can be traced to the document.

## Run locally

```bash
pip install pillow
bash scripts/fetch.sh
python scripts/build.py --upstream work/ftm --legislators work/legislators-current.json \
  --weball work/weball26.zip --cn work/cn26.zip --factchecks data/factchecks.json --out site
python -m http.server 8000 --directory site
```

## Deploying

The workflow builds on every push to `main`, on a daily schedule, and on demand, and deploys `site/` to GitHub Pages. One-time setup in the repository settings: **Settings → Pages → Build and deployment → Source: GitHub Actions**.

### Custom domain

1. Settings → Pages → Custom domain: enter your domain (e.g. `followtheads.org` or `ads.yourdomain.com`) and save. Turn on *Enforce HTTPS* once the certificate is issued (a few minutes to an hour).
2. At your DNS provider:
   - for a subdomain (`ads.example.com`): a `CNAME` record pointing to `<your-github-user-or-org>.github.io`
   - for an apex domain (`example.com`): four `A` records to `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153` (and optionally `AAAA` records to `2606:50c0:8000::153` … `8003::153`), plus a `CNAME` for `www` to `<user-or-org>.github.io`
3. GitHub verifies the DNS and serves the site at the domain. The setting persists across deployments.

Any other static host works too (Netlify, Cloudflare Pages, Vercel, an S3 bucket): point it at the `site/` folder, there is no build step on the host side.

## Editing the fact-check list

`data/factchecks.json` is an array of objects:

```json
{"race_id":"S-OH","sponsor":"Senate Leadership Fund","ad_title_or_description":"…","targets":"Sherrod Brown",
 "for_or_against":"against","claim":"…","verdict":"Pants on Fire","outlet":"PolitiFact","url":"https://…","date":"2026-10-14"}
```

`race_id` is `S-<state>` for Senate and `H-<state>-<district>` for House (two-digit district, `00` for at-large). Keep `verdict` as the outlet's own wording; the page colors it by keyword only.

## Caveats

Spending shows where groups place their bets, not why; nothing here is a forecast. Amounts filed on 24/48-hour notices are often estimates trued up later. Party coordinated spending lags by weeks. Nominees are identified from where general-election outside money is aimed; where none exists, the top fundraiser is used and marked as inferred.
