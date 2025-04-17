# Bridge Fees Tracker

A Python tool that compares cross-chain bridge fees between Across and Hop protocols, supporting major tokens (USDC, ETH, USDT) and L2 chains (Optimism, Arbitrum, Base). The tool provides real-time fee comparisons with detailed breakdowns of components like relay fees, LP fees, bonder fees, and AMM fees, helping users make cost-effective bridging decisions.

## Features

- Compare bridge fees across multiple protocols
- Support for multiple tokens (USDC, ETH, USDT)
- Support for multiple chains (Ethereum, Optimism, Arbitrum, Polygon, Base, zkSync)
- Detailed fee breakdowns
- Easy to extend for additional protocols

## Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/bridge-fees-tracker.git
cd bridge-fees-tracker
```

2. Install dependencies:
```bash
pip3 install -r requirements.txt
```

3. Create a `.env` file with your Infura API key:
```bash
INFURA_KEY=your_infura_key_here
```

## Usage

Run the script with default parameters:
```bash
python3 bridge_fees_tracker.py
```

The script will output a comparison table showing fees across different bridge protocols.

## Example Output

```
+----------+------------+------------------------+
| Protocol | Total Fee  | Fee Breakdown         |
+----------+------------+------------------------+
| Across   | 2.5 USDC  | {"lp_fee": 2,         |
|          |           |  "relayer_fee": 0.5}   |
+----------+------------+------------------------+
| Hop      | 3.0 USDC  | {"lp_fee": 2.5,       |
|          |           |  "amm_fee": 0.5}       |
+----------+------------+------------------------+
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License 