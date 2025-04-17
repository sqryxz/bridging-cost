import os
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from tabulate import tabulate
import pandas as pd
import time
import random
from functools import wraps
import logging
import sys
from web3 import Web3
from eth_abi import decode

# Set up logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Web3 and contract addresses
INFURA_KEY = os.getenv('INFURA_KEY')
w3 = Web3(Web3.HTTPProvider(f'https://mainnet.infura.io/v3/{INFURA_KEY}'))

# Contract addresses and ABIs
ACROSS_QUOTER = '0x...'  # Add Across quoter contract address
HOP_QUOTER = '0x...'     # Add Hop quoter contract address

# Supported tokens and chains
TOKENS = ['USDC', 'ETH', 'USDT']
CHAINS = {
    'Ethereum': 1,
    'Optimism': 10,
    'Arbitrum': 42161,
    'Polygon': 137,
    'Base': 8453,
    'zkSync': 324
}

class Cache:
    def __init__(self, expiry_minutes=5):  # Across docs recommend max 300 seconds (5 minutes) cache
        self.cache = {}
        self.expiry_minutes = expiry_minutes

    def get(self, key):
        if key in self.cache:
            entry = self.cache[key]
            if datetime.now() < entry['expiry']:
                logger.debug(f"Cache hit for key: {key}")
                return entry['data']
            else:
                logger.debug(f"Cache expired for key: {key}")
                del self.cache[key]
        return None

    def set(self, key, data):
        expiry = datetime.now() + timedelta(minutes=self.expiry_minutes)
        self.cache[key] = {
            'data': data,
            'expiry': expiry
        }
        logger.debug(f"Cached data for key: {key}")

def retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10, exponential_base=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None

            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if retry < max_retries - 1:
                        jitter = random.uniform(0, 0.1) * delay
                        sleep_time = min(delay + jitter, max_delay)
                        logger.warning(f"Attempt {retry + 1} failed. Retrying in {sleep_time:.2f} seconds...")
                        time.sleep(sleep_time)
                        delay *= exponential_base
            
            logger.error(f"All {max_retries} attempts failed. Last error: {str(last_exception)}")
            return None
        return wrapper
    return decorator

class BridgeFeeTracker:
    def __init__(self):
        self.supported_tokens = ['USDC', 'ETH', 'USDT']
        self.chains = {
            'ethereum': 1,
            'optimism': 10,
            'arbitrum': 42161,
            'polygon': 137,
            'base': 8453,
            'zksync': 324
        }
        # Token addresses on Ethereum mainnet
        self.token_addresses = {
            'USDC': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',  # USDC on Ethereum
            'USDT': '0xdac17f958d2ee523a2206206994597c13d831ec7',  # USDT on Ethereum
            'ETH': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'   # WETH on Ethereum
        }
        # Hop Protocol chain names
        self.hop_chains = {
            1: 'ethereum',
            10: 'optimism',
            42161: 'arbitrum',
            137: 'polygon',
            8453: 'base'
        }
        # Hop Protocol token names
        self.hop_tokens = {
            'ETH': 'ETH',
            'USDC': 'USDC',
            'USDT': 'USDT'
        }
        self.demo_wallet = '0x0000000000000000000000000000000000000000'
        self.cache = Cache(expiry_minutes=5)

    def _get_cache_key(self, protocol, token, from_chain, to_chain, amount):
        return f"{protocol}:{token}:{from_chain}:{to_chain}:{amount}"

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    def _fetch_across_suggested_fees(self, token, from_chain, to_chain, amount):
        """Internal method to fetch Across Protocol suggested fees"""
        url = "https://across.to/api/suggested-fees"
        decimals = 6 if token in ['USDC', 'USDT'] else 18
        amount_in_decimals = str(int(amount * 10**decimals))
        
        params = {
            "token": self.token_addresses[token],
            "destinationChainId": str(self.chains[to_chain]),
            "amount": amount_in_decimals,
            "originChainId": str(self.chains[from_chain])
        }
        
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        fee_details = {}
        
        # Calculate relay fee
        relay_fee_pct = float(data.get('relayFeePct', 0)) / 1e18  # Convert from wei format
        fee_details['relay_fee'] = round(amount * relay_fee_pct, 6)
        
        # Calculate LP fee
        lp_fee_pct = float(data.get('lpFeePct', 0)) / 1e18  # Convert from wei format
        fee_details['lp_fee'] = round(amount * lp_fee_pct, 6)
        
        # Calculate total fee
        fee_details['total_fee'] = round(fee_details['relay_fee'] + fee_details['lp_fee'], 6)
        
        return fee_details

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    def _fetch_across_limits(self, token, from_chain, to_chain):
        """Internal method to fetch Across Protocol limits"""
        url = "https://across.to/api/limits"
        
        params = {
            "token": self.token_addresses[token],
            "destinationChainId": str(self.chains[to_chain]),
            "originChainId": str(self.chains[from_chain])
        }
        
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_across_fee(self, token, from_chain, to_chain, amount):
        """Fetch fee estimate from Across Protocol with caching"""
        cache_key = self._get_cache_key('across', token, from_chain, to_chain, amount)
        cached_result = self.cache.get(cache_key)
        
        if cached_result is not None:
            logger.info("Using cached Across Protocol fee")
            return cached_result
        
        try:
            # First check if amount is within limits
            limits = self._fetch_across_limits(token, from_chain, to_chain)
            decimals = 6 if token in ['USDC', 'USDT'] else 18
            min_deposit = float(limits.get('minDeposit', 0)) / (10**decimals)
            max_deposit = float(limits.get('maxDeposit', 0)) / (10**decimals)
            
            if amount < min_deposit:
                logger.warning(f"Amount {amount} is below minimum deposit of {min_deposit}")
                return None
            if amount > max_deposit:
                logger.warning(f"Amount {amount} is above maximum deposit of {max_deposit}")
                return None
            
            # Get fee if amount is within limits
            fee = self._fetch_across_suggested_fees(token, from_chain, to_chain, amount)
            if fee is not None:
                self.cache.set(cache_key, fee)
            return fee
        except Exception as e:
            logger.error(f"Error fetching Across fee: {e}")
            return None

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    def _fetch_hop_fee(self, token, from_chain, to_chain, amount):
        """Internal method to fetch Hop Protocol fee with retry logic"""
        # Convert chain IDs to Hop's format
        from_chain_name = self.hop_chains.get(self.chains[from_chain])
        to_chain_name = self.hop_chains.get(self.chains[to_chain])
        
        if not from_chain_name or not to_chain_name:
            logger.error(f"Unsupported chain for Hop Protocol: {from_chain} -> {to_chain}")
            return None
            
        hop_token = self.hop_tokens.get(token)
        if not hop_token:
            logger.error(f"Unsupported token for Hop Protocol: {token}")
            return None
            
        url = "https://api.hop.exchange/v1/quote"  # Official API endpoint
        decimals = 6 if token in ['USDC', 'USDT'] else 18
        amount_in_wei = str(int(amount * 10**decimals))
        
        params = {
            "amount": amount_in_wei,
            "token": hop_token,
            "fromChain": from_chain_name,
            "toChain": to_chain_name,
            "slippage": "0.5",  # 0.5% slippage as per docs
            "network": "mainnet"
        }
        
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            # Log detailed information about the request
            logger.debug(f"Hop Protocol API request URL: {response.url}")
            logger.debug(f"Hop Protocol API response status: {response.status_code}")
            
            if response.status_code == 429:
                logger.error("Rate limit exceeded for Hop Protocol API.")
                time.sleep(5)  # Add additional delay for rate limiting
                return None
                
            response.raise_for_status()
            data = response.json()
            
            if isinstance(data, dict) and 'error' in data:
                logger.error(f"Hop Protocol API error: {data['error']}")
                return None
                
            # Parse response according to official docs
            if isinstance(data, dict):
                fee_details = {}
                
                # Get bonder fee
                if 'bonderFee' in data:
                    fee_details['bonder_fee'] = float(data['bonderFee']) / 10**decimals
                    
                # Calculate total fee including AMM fees
                if 'amountIn' in data and 'estimatedRecieved' in data:
                    amount_in = float(data['amountIn']) / 10**decimals
                    estimated_received = float(data['estimatedRecieved']) / 10**decimals
                    total_fee = round(amount_in - estimated_received, 6)
                    
                    # Calculate AMM fee (total fee - bonder fee)
                    if 'bonder_fee' in fee_details:
                        fee_details['amm_fee'] = round(total_fee - fee_details['bonder_fee'], 6)
                    
                    fee_details['total_fee'] = total_fee
                    
                    # Validate fee is reasonable
                    if total_fee < 0 or total_fee > amount:
                        logger.warning(f"Suspicious fee amount: {total_fee} {token}")
                        return None
                            
                    return fee_details
            
            logger.error("Invalid response format from Hop Protocol API")
            logger.debug(f"Response data: {data}")
            return None
            
        except requests.exceptions.Timeout:
            logger.error("Timeout while fetching Hop Protocol fee")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error while fetching Hop Protocol fee: {str(e)}")
            return None
        except (ValueError, KeyError, TypeError) as e:
            logger.error(f"Error parsing Hop Protocol API response: {str(e)}")
            return None

    def get_hop_fee(self, token, from_chain, to_chain, amount):
        """Fetch fee estimate from Hop Protocol with caching"""
        cache_key = self._get_cache_key('hop', token, from_chain, to_chain, amount)
        cached_result = self.cache.get(cache_key)
        
        if cached_result is not None:
            logger.info("Using cached Hop Protocol fee")
            return cached_result
        
        try:
            fee = self._fetch_hop_fee(token, from_chain, to_chain, amount)
            if fee is not None:
                self.cache.set(cache_key, fee)
            return fee
        except Exception as e:
            logger.error(f"Error fetching Hop Protocol fee: {e}")
            return None

    def compare_fees(self, token='USDC', from_chain='ethereum', to_chain='optimism', amount=1000):
        """Compare fees between different protocols"""
        logger.info(f"Fetching fees for {amount} {token} from {from_chain.capitalize()} to {to_chain.capitalize()}...")
        
        # Add validation for supported tokens and chains
        if token not in self.supported_tokens:
            logger.error(f"Unsupported token: {token}")
            return None
        if from_chain not in self.chains:
            logger.error(f"Unsupported source chain: {from_chain}")
            return None
        if to_chain not in self.chains:
            logger.error(f"Unsupported destination chain: {to_chain}")
            return None
            
        # Add delay between API calls to prevent rate limiting
        time.sleep(2)
        across_fees = self.get_across_fee(token, from_chain, to_chain, amount)
        time.sleep(2)  # Increased delay between different API calls
        hop_fees = self.get_hop_fee(token, from_chain, to_chain, amount)

        # Check if both APIs failed
        if across_fees is None and hop_fees is None:
            logger.error("Failed to fetch fees from both protocols")
            print("\nError: Unable to fetch fees from any protocol at this time.")
            return None

        # Create detailed fee breakdown
        comparison_data = {
            'Protocol': [],
            'Total Fee': [],
            'Components': []
        }

        # Add Across Protocol fees
        if across_fees is not None:
            comparison_data['Protocol'].append('Across Protocol')
            comparison_data['Total Fee'].append(f"{across_fees['total_fee']:.6f} {token}")
            components = [
                f"Relay Fee: {across_fees['relay_fee']:.6f} {token}",
                f"LP Fee: {across_fees['lp_fee']:.6f} {token}"
            ]
            comparison_data['Components'].append('\n'.join(components))
        else:
            comparison_data['Protocol'].append('Across Protocol')
            comparison_data['Total Fee'].append('N/A')
            comparison_data['Components'].append('N/A')

        # Add Hop Protocol fees
        if hop_fees is not None:
            comparison_data['Protocol'].append('Hop Protocol')
            comparison_data['Total Fee'].append(f"{hop_fees['total_fee']:.6f} {token}")
            components = [
                f"Bonder Fee: {hop_fees.get('bonder_fee', 0):.6f} {token}",
                f"AMM Fee: {hop_fees.get('amm_fee', 0):.6f} {token}"
            ]
            comparison_data['Components'].append('\n'.join(components))
        else:
            comparison_data['Protocol'].append('Hop Protocol')
            comparison_data['Total Fee'].append('N/A')
            comparison_data['Components'].append('N/A')

        df = pd.DataFrame(comparison_data)
        print(f"\nBridge Fee Comparison")
        print(f"Token: {token}")
        print(f"From: {from_chain.capitalize()}")
        print(f"To: {to_chain.capitalize()}")
        print(f"Amount: {amount} {token}")
        print("\n" + tabulate(df, headers='keys', tablefmt='grid', showindex=False))
        
        # Log success/failure statistics
        success_count = sum(1 for fee in [across_fees, hop_fees] if fee is not None)
        logger.info(f"Successfully fetched {success_count}/2 protocol fees")
        
        return df

def main():
    tracker = BridgeFeeTracker()
    
    print("\n=== Bridge Fee Comparison Tool ===")
    
    try:
        # Compare fees for different scenarios with error handling
        scenarios = [
            {'token': 'USDC', 'from_chain': 'ethereum', 'to_chain': 'optimism', 'amount': 1000},
            {'token': 'ETH', 'from_chain': 'ethereum', 'to_chain': 'arbitrum', 'amount': 1}
        ]
        
        for scenario in scenarios:
            result = tracker.compare_fees(**scenario)
            if result is None:
                logger.warning(f"Failed to compare fees for scenario: {scenario}")
            time.sleep(3)  # Add delay between scenarios
            
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        print("\nAn unexpected error occurred. Please check the logs for details.")
        sys.exit(1)

if __name__ == "__main__":
    main() 