import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from collections import defaultdict

# Configuration
GRAPHQL_ENDPOINT = "https://api.thegraph.com/subgraphs/name/ekubo/eth-usdc-pool"
POOL_ADDRESS = "0x..."  # Ekubo ETH/USDC pool address
LAUNCH_BLOCK = 12345678  # Pool launch block
INITIAL_INVESTMENT = 1000  # $1000 normalization
MIN_INVESTMENT_DAYS = 60  # Minimum days invested
TOP_USERS_COUNT = 10

# GraphQL queries
QUERY_POOL_DATA = """
query PoolData {
  pool(id: "%s") {
    id
    feeTier
    token0 { symbol address decimals }
    token1 { symbol address decimals }
    startTime
    totalValueLockedUSD
  }
}
""" % POOL_ADDRESS

QUERY_USER_POSITIONS = """
query UserPositions($user: ID!) {
  positions(where: {owner: $user, pool: "%s"}) {
    id
    depositedToken0
    depositedToken1
    withdrawnToken0
    withdrawnToken1
    rebalances { id timestamp }
    deposits { timestamp amount0 amount1 }
    withdrawals { timestamp amount0 amount1 }
  }
}
""" % POOL_ADDRESS

QUERY_PRICE_DATA = """
query PriceData($fromBlock: Int!) {
  ethUsdcPairs(where: {token0: "0x...", token1: "0x..."}, block: {number_gte: $fromBlock}) {
    token0Price
    token1Price
    block {
      number
      timestamp
    }
  }
}
"""

def fetch_graphql(query, variables=None):
    response = requests.post(
        GRAPHQL_ENDPOINT,
        json={"query": query, "variables": variables or {}}
    )
    response.raise_for_status()
    return response.json()["data"]

def get_pool_data():
    data = fetch_graphql(QUERY_POOL_DATA)
    pool = data["pool"]
    return {
        "fee_tier": pool["feeTier"],
        "token0": pool["token0"],
        "token1": pool["token1"],
        "start_time": datetime.fromtimestamp(int(pool["startTime"]))
    }

def get_user_list():
    # Get all users who deposited in the pool
    query = """
    query AllUsers {
      deposits(where: {pool: "%s"}) {
        owner
      }
    }
    """ % POOL_ADDRESS
    data = fetch_graphql(query)
    users = set(deposit["owner"] for deposit in data["deposits"])
    return list(users)

def get_price_history():
    # Get ETH/USDC price history from Uniswap or other source
    # This is a simplified version - in practice, use a proper price oracle
    end_time = datetime.now()
    start_time = get_pool_data()["start_time"]
    
    # Sample implementation using CoinGecko API
    response = requests.get(
        "https://api.coingecko.com/api/v3/coins/ethereum/market_chart",
        params={
            "vs_currency": "usd",
            "days": (end_time - start_time).days,
            "interval": "hourly"
        }
    )
    prices = response.json()["prices"]
    return pd.DataFrame(prices, columns=["timestamp", "price"])

def calculate_user_performance(user_address):
    # Get user's position data
    variables = {"user": user_address}
    data = fetch_graphql(QUERY_USER_POSITIONS, variables)
    positions = data["positions"]
    
    if not positions:
        return None
    
    position = positions[0]  # Assuming one position per user-pool pair
    
    # Calculate investment timeline
    deposits = pd.DataFrame(position["deposits"])
    withdrawals = pd.DataFrame(position["withdrawals"])
    
    if deposits.empty:
        return None
    
    # Normalize investment to $1000 at first deposit
    first_deposit = deposits.iloc[0]
    first_deposit_value = (
        float(first_deposit["amount0"]) * current_eth_price +
        float(first_deposit["amount1"])
    )
    normalization_factor = INITIAL_INVESTMENT / first_deposit_value
    
    # Calculate portfolio value over time
    timeline = []
    current_tokens = {"token0": 0, "token1": 0}
    
    # Process all events chronologically
    events = []
    for _, deposit in deposits.iterrows():
        events.append({
            "timestamp": datetime.fromtimestamp(int(deposit["timestamp"])),
            "type": "deposit",
            "amount0": float(deposit["amount0"]),
            "amount1": float(deposit["amount1"])
        })
    for _, withdraw in withdrawals.iterrows():
        events.append({
            "timestamp": datetime.fromtimestamp(int(withdraw["timestamp"])),
            "type": "withdraw",
            "amount0": -float(withdraw["amount0"]),
            "amount1": -float(withdraw["amount1"])
        })
    
    events.sort(key=lambda x: x["timestamp"])
    
    for event in events:
        if event["type"] == "deposit":
            current_tokens["token0"] += event["amount0"] * normalization_factor
            current_tokens["token1"] += event["amount1"] * normalization_factor
        else:  # withdraw
            current_tokens["token0"] -= abs(event["amount0"]) * normalization_factor
            current_tokens["token1"] -= abs(event["amount1"]) * normalization_factor
        
        # Get ETH price at this timestamp
        eth_price = get_historical_price(event["timestamp"])
        portfolio_value = (
            current_tokens["token0"] * eth_price +
            current_tokens["token1"]
        )
        
        timeline.append({
            "timestamp": event["timestamp"],
            "value": portfolio_value,
            "tokens": current_tokens.copy()
        })
    
    return pd.DataFrame(timeline)

def get_historical_price(timestamp):
    # Implement proper historical price lookup
    price_data = get_price_history()
    closest_price = price_data.iloc[
        (price_data["timestamp"] - timestamp).abs().argsort()[:1]
    ]["price"].values[0]
    return closest_price

def calculate_hodl_performance():
    # Calculate HODL performance: $1000 invested at pool launch
    price_history = get_price_history()
    initial_price = price_history.iloc[0]["price"]
    
    hodl_values = []
    for _, row in price_history.iterrows():
        eth_value = INITIAL_INVESTMENT / row["price"]
        hodl_values.append({
            "timestamp": datetime.fromtimestamp(row["timestamp"] / 1000),
            "value": eth_value * row["price"]  # Should be $1000 if no rebalancing
        })
    
    return pd.DataFrame(hodl_values)

def analyze_top_performers(users_data):
    # Filter users who were invested for at least 60 days
    qualified_users = []
    for user, data in users_data.items():
        if data is None or data.empty:
            continue
        investment_duration = (data["timestamp"].max() - data["timestamp"].min()).days
        if investment_duration >= MIN_INVESTMENT_DAYS:
            final_value = data.iloc[-1]["value"]
            profit = final_value - INITIAL_INVESTMENT
            qualified_users.append({
                "user": user,
                "profit": profit,
                "final_value": final_value,
                "rebalances": len(get_user_rebalances(user)),
                "investment_days": investment_duration
            })
    
    # Sort by profit and get top N
    top_users = sorted(
        qualified_users,
        key=lambda x: x["profit"],
        reverse=True
    )[:TOP_USERS_COUNT]
    
    return top_users

def get_user_rebalances(user_address):
    # Get rebalance count for user
    variables = {"user": user_address}
    data = fetch_graphql(QUERY_USER_POSITIONS, variables)
    positions = data["positions"]
    if not positions:
        return []
    return positions[0]["rebalances"]

def plot_performance(users_data, hodl_data):
    plt.figure(figsize=(12, 8))
    
    # Plot user performances
    for user, data in users_data.items():
        if data is None or data.empty:
            continue
        plt.plot(
            data["timestamp"],
            data["value"],
            alpha=0.6,
            linewidth=1,
            label=f"User {user[:6]}..."
        )
    
    # Plot HODL line
    plt.plot(
        hodl_data["timestamp"],
        hodl_data["value"],
        color="black",
        linewidth=3,
        label="HODL $1000"
    )
    
    plt.title("LP Performance: Capital Value vs Time")
    plt.xlabel("Date")
    plt.ylabel("Portfolio Value (USD)")
    plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("lp_performance.png")
    plt.close()

def plot_top_users_analysis(top_users):
    # Plot 1: Number of rebalances
    plt.figure(figsize=(12, 6))
    users = [f"User {u['user'][:6]}..." for u in top_users]
    rebalances = [u["rebalances"] for u in top_users]
    
    plt.bar(users, rebalances)
    plt.title("Number of Rebalances - Top Performers")
    plt.xlabel("User")
    plt.ylabel("Number of Rebalances")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig("rebalances.png")
    plt.close()
    
    # Plot 2: Annualized Profit %
    plt.figure(figsize=(12, 6))
    annualized_profits = []
    for user in top_users:
        # Annualized profit = (final_value/initial_investment)^(365/days) - 1
        annual_return = (
            (user["final_value"] / INITIAL_INVESTMENT) ** 
            (365 / user["investment_days"]) - 1
        ) * 100
        annualized_profits.append(annual_return)
    
    plt.bar(users, annualized_profits)
    plt.title("Annualized Profit % - Top Performers")
    plt.xlabel("User")
    plt.ylabel("Annualized Profit (%)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig("annualized_profit.png")
    plt.close()

def main():
    # Get pool data
    pool_data = get_pool_data()
    print(f"Analyzing pool: {pool_data['token0']['symbol']}/{pool_data['token1']['symbol']}")
    
    # Get all users
    users = get_user_list()
    print(f"Found {len(users)} users")
    
    # Calculate performance for each user
    users_data = {}
    for user in users:
        users_data[user] = calculate_user_performance(user)
    
    # Get HODL data
    hodl_data = calculate_hodl_performance()
    
    # Plot overall performance
    plot_performance(users_data, hodl_data)
    
    # Analyze top performers
    top_users = analyze_top_performers(users_data)
    print(f"Top {len(top_users)} performers identified")
    
    # Plot top users analysis
    plot_top_users_analysis(top_users)
    
    # Save results to CSV
    results_df = pd.DataFrame(top_users)
    results_df.to_csv("top_performers.csv", index=False)
    
    print("Analysis complete. Files generated:")
    print("- lp_performance.png")
    print("- rebalances.png")
    print("- annualized_profit.png")
    print("- top_performers.csv")

if __name__ == "__main__":
    main()
