# Lowe's Deal Finder

A phone-first Lowe's deal finder built to prioritize fresh results, remove duplicates, remember prices, and avoid stale deal links.

## What it does

- Runs automatically every hour at minute 17 and can also be run manually from GitHub Actions.
- Uses a Lowe's-specific DealNews RSS stream plus additional current deal feeds because Lowe's strips its product grid from GitHub cloud runners.
- Keeps DealNews attribution/referral links intact and uses direct Lowe's product links whenever one is available.
- Includes a short-lived direct-Lowe's Back Aisle safety net for currently verified clearance products.
- Ignores feed items older than 5 days.
- Tracks `first_seen`, `last_seen`, latest price, lowest observed price, and real price drops.
- Marks results `NEW`, `PRICE DROP`, `SEEN BEFORE`, or `UNVERIFIED`.
- Retains a temporarily missing deal for up to 36 hours as `UNVERIFIED`, then drops it.
- Never treats a free-shipping minimum such as `$45` as a product's regular price.
- Phone UI supports search, category filtering, discount filtering, sorting, and direct deal opening.

## Data sources

- DealNews Lowe's feed — current Lowe's deal discovery. Feed attribution and referral links are preserved.
- DealNews latest and Home & Garden feeds — filtered to Lowe's mentions.
- Lowe's Back Aisle — short-lived direct-product safety net and a permanent navigation link in the app.
- Lowe's Daily Deals and Weekly Ad — linked directly in the app.

## Run it

Open **Actions → Scan Lowe's Deals → Run workflow**. The resulting `data/deals.json` is committed back to the repository automatically.

## Put it on your iPhone

1. Connect this GitHub repository to Vercel as a static project; there is no build command.
2. Open the deployed URL in Safari.
3. Tap **Share → Add to Home Screen**.

## Notes

Lowe's prices, promotions, inventory and store availability can change by location. The app shows only price/discount information that can be supported by the feed or by an observed price change; confirm the final price on Lowe's before buying.
