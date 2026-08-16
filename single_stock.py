"""
single_stock.py
Analyze any individual stock symbol through the full agent pipeline.
Usage: python single_stock.py RELIANCE.NS
"""

import sys
import json
import os
import time
from datetime import datetime

# Import from existing modules
import data_sources
import llm

# Try to import scoring for fallback
scoring = None
try:
    import scoring
except ImportError:
    pass


def analyze_single_stock(symbol, provider=None):
    """
    Run the full agent pipeline on a single stock symbol.

    Args:
        symbol: Stock ticker (e.g., "RELIANCE.NS", "TCS.NS", "INFY.NS")
        provider: LLM provider override (optional)

    Returns:
        dict: Full analysis result with scores, verdict, and metadata
    """
    print(f"\n{'='*60}")
    print(f"  Analyzing: {symbol.upper()}")
    print(f"{'='*60}\n")

    # Step 1: Fetch data bundle
    print("[1/5] Scout: Fetching stock data...")
    try:
        bundle = data_sources.build_bundles_for_tickers([symbol], cap_segment="individual")
        if not bundle:
            print(f"❌ Could not fetch data for {symbol}")
            return None
        evidence = bundle[0]
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return None

    print(f"   ✅ Data fetched: {evidence.get('name', symbol)}")
    price = evidence.get('price', {})
    print(f"   📊 Live Price: ₹{price.get('live', 'N/A')} | Change: {price.get('day_change_pct', 'N/A')}%")
    time.sleep(0.3)

    # Step 2: Technician analysis
    print("[2/5] Technician: Reading price action, RVOL & trend...")
    tech = evidence.get('technicals', {})
    print(f"   📈 RVOL: {tech.get('rvol', 'N/A')} | Trend: {tech.get('trend', 'N/A')}")
    time.sleep(0.3)

    # Step 3: Fundamentalist analysis
    print("[3/5] Fundamentalist: Weighing valuation & analyst targets...")
    analyst = evidence.get('analyst', {})
    print(f"   🎯 Target: ₹{analyst.get('target', 'N/A')} | Upside: {analyst.get('upside_pct', 'N/A')}%")
    time.sleep(0.3)

    # Step 4: Newsdesk analysis
    print("[4/5] Newsdesk: Pulling live news & scoring sentiment...")
    news = evidence.get('news', {})
    print(f"   📰 Headlines: {news.get('total', 0)} | Positive: {news.get('positive', 0)} | Negative: {news.get('negative', 0)}")
    time.sleep(0.3)

    # Step 5: Bull/Bear debate + Judge verdict
    print("[5/5] Bull vs Bear: Running LLM debate...")
    try:
        result = llm.evaluate(evidence, provider=provider)
        engine_used = result.get('_engine', 'unknown')
    except Exception as e:
        print(f"   ⚠️ LLM failed ({e}), falling back to deterministic scoring...")
        if scoring:
            result = scoring.evaluate_deterministic(evidence)
            result['_engine'] = 'deterministic'
            engine_used = 'deterministic'
        else:
            print("❌ No scoring module available")
            return None

    # Build the full result
    scores = result.get('scores', {})
    verdict = result.get('verdict', {})

    output = {
        'symbol': symbol.upper(),
        'name': evidence.get('name', symbol),
        'timestamp': datetime.now().isoformat(),
        'engine': engine_used,
        'price': price,
        'technicals': tech,
        'analyst': analyst,
        'news': news,
        'scores': scores,
        'verdict': verdict,
        'summary': {
            'bull_score': scores.get('bull', {}).get('score', 0),
            'bear_score': scores.get('bear', {}).get('score', 0),
            'technician_score': scores.get('technician', {}).get('score', 0),
            'fundamentalist_score': scores.get('fundamentalist', {}).get('score', 0),
            'newsdesk_score': scores.get('newsdesk', {}).get('score', 0),
            'final_verdict': verdict.get('verdict', 'WATCH'),
            'confidence': verdict.get('confidence', 0),
            'winner': verdict.get('winner', '-'),
            'rationale': verdict.get('rationale', ''),
            'key_catalyst': verdict.get('key_catalyst', ''),
            'net_score': verdict.get('net', 0),
        }
    }

    return output


def print_analysis(result):
    """Pretty-print the analysis result."""
    if not result:
        return

    s = result['summary']
    v = result['verdict']

    print(f"\n{'='*60}")
    print(f"  📊 ANALYSIS RESULT: {result['symbol']} ({result['name']})")
    print(f"{'='*60}")

    # Price info
    price = result['price']
    print(f"\n💰 Price: ₹{price.get('live', 'N/A')} | Change: {price.get('day_change_pct', 'N/A')}%")

    # Scores
    print(f"\n📈 Agent Scores:")
    print(f"   🐂 Bull:           {s['bull_score']}/100")
    print(f"   🐻 Bear:           {s['bear_score']}/100")
    print(f"   📊 Technician:     {s['technician_score']}/100")
    print(f"   🧮 Fundamentalist: {s['fundamentalist_score']}/100")
    print(f"   📰 Newsdesk:      {s['newsdesk_score']}/100")
    print(f"   ─────────────────────────")
    print(f"   Net Score:        {s['net_score']}")

    # Verdict
    verdict_color = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(s['final_verdict'], "⚪")
    print(f"\n{verdict_color} VERDICT: {s['final_verdict']}")
    print(f"   Confidence: {s['confidence']}/10")
    print(f"   Winner: {s['winner']}")
    print(f"   Catalyst: {s['key_catalyst']}")
    print(f"\n📝 Rationale:")
    print(f"   {s['rationale']}")

    print(f"\n⚙️  Engine: {result['engine']}")
    print(f"🕐 Time: {result['timestamp']}")
    print(f"{'='*60}\n")


def save_result(result, symbol):
    """Save result to JSON file."""
    filename = f"analysis_{symbol.upper().replace('.', '_')}.json"
    with open(filename, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"💾 Result saved to: {filename}")
    return filename


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Analyze a single stock with AgentDesk')
    parser.add_argument('symbol', help='Stock symbol (e.g., RELIANCE.NS, TCS.NS)')
    parser.add_argument('--provider', default=None, help='LLM provider (openai/anthropic/claude_code)')
    parser.add_argument('--save', action='store_true', help='Save result to JSON file')
    parser.add_argument('--json', action='store_true', help='Output raw JSON only')
    args = parser.parse_args()

    # Run analysis
    result = analyze_single_stock(args.symbol, provider=args.provider)

    if not result:
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_analysis(result)

    if args.save:
        save_result(result, args.symbol)

    # Exit with appropriate code
    verdict = result['summary']['final_verdict']
    if verdict == 'BUY':
        sys.exit(0)
    elif verdict == 'WATCH':
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
