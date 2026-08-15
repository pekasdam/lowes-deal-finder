# Lowe's Deal Finder

A phone-first Lowe's deal finder that scans official Lowe's savings pages, removes duplicates, tracks price history, cleans product links, and drops stale results.

## What it does

- Scans Lowe's **Daily Deals**, **Savings**, and **Back Aisle / Clearance** sources.
- Discovers current Lowe's deal-collection pages automatically instead of hard-coding old campaigns.
- Uses a real Chromium browser through Playwright so dynamic product cards can render.
- Canonicalizes every item to its direct `lowes.com/pd/...` product URL.
- Dedupe key: Lowe's item ID when available, otherwise a stable URL hash.
- Tracks `first_seen`, `last_seen`, latest price, lowest observed price, and price drops.
- Marks results `NEW`, `PRICE DROP`, `SEEN BEFORE`, or `UNVERIFIED`.
- Ages out deals that haven't been verified within 48 hours.
- Runs hourly at minute 17 and can also be run manually from GitHub Actions.
- PWA-ready for iPhone **Add to Home Screen**.

## First setup

1. In GitHub: **Settings → Actions → General → Workflow permissions → Read and write permissions**.
2. Open **Actions → Scan Lowe's Deals → Run workflow** for the first scan.
3. Connect the repo to Vercel. This project is static and needs no build command.
4. Open the Vercel URL in Safari → Share → **Add to Home Screen**.

## Deal threshold

The scanner stores broad candidates starting around 15% or obvious Lowe's deal markers. The app defaults to showing 40%+ so the list stays useful. You can change the filter instantly on the phone.

## Current Lowe's sources

- Daily Deals: one-day, online-only offers.
- Savings hub: current promotions and deal collections.
- The Back Aisle: Lowe's clearance destination.
- Weekly Ad: linked from the app for local-store promotions.

## Notes

Lowe's prices, promotions, inventory and store availability can change. A deal should always be confirmed on the direct Lowe's product page before buying.
