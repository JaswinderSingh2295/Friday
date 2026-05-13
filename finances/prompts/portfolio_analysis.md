You are analyzing a personal investment portfolio as of {today}.
Data comes directly from brokerage APIs (via Plaid) and reflects real holdings.
Be specific — use actual tickers and dollar amounts.
Do not give specific buy/sell recommendations; frame insights as observations and questions.

## Portfolio Data

{portfolio_data}

**Total portfolio value (sum of holdings):** ${total_value:,.2f}

**Asset type breakdown:**

{asset_breakdown}

## Analysis Request

Please provide:

### 1. Portfolio Health Score (0–100)

One-line rationale.

### 2. Allocation Analysis

- Is the overall allocation appropriate for a long-term investor?
- Notable concentration risks (single stock, sector, or account)?
- Diversification across asset types and geographies?

### 3. Notable Positions

- Flag any positions above {notable_pct:.0f}% of portfolio
- Positions that stand out as interesting or unusual?
- Unrealized gains/losses worth noting?

### 4. Recent Activity

- What do the last {txn_days} days of transactions reveal about investing behavior?
- Any patterns (frequent trading, consistent buying, etc.)?

### 5. Questions to Consider

3–5 thoughtful questions the investor should ask themselves, focused on what the data reveals that deserves reflection.

### 6. One Thing to Look Into

The single most actionable area to research further — not financial advice, just where the data suggests focusing attention.

---

Be direct and specific. Use actual tickers and dollar amounts.
This is for personal informational use only — not investment advice.
