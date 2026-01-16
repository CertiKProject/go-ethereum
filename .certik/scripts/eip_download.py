#!/usr/bin/env python3
"""
EIP Downloader - Download Ethereum Improvement Proposals from GitHub
"""

import os
import sys
import requests
from pathlib import Path
from typing import List, Optional


class EIPDownloader:
    """Download EIPs from ethereum/EIPs GitHub repository"""
    
    BASE_URL = "https://raw.githubusercontent.com/ethereum/EIPs/master"
    
    def __init__(self, output_dir: Optional[str] = None):
        """
        Initialize the EIP downloader.
        
        Args:
            output_dir: Directory to save downloaded EIPs (default: ../eips relative to script)
        """
        if output_dir is None:
            # Default to ../eips relative to this script's location
            script_dir = os.path.dirname(os.path.abspath(__file__))
            output_dir = os.path.join(script_dir, "..", "eips")
            output_dir = os.path.abspath(output_dir)
        
        self.output_dir = output_dir
        self.session = requests.Session()
        
    def _ensure_output_dir(self):
        """Ensure output directory exists"""
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        
    def _get_eip_filename(self, eip_number: int) -> str:
        """Generate EIP filename from number"""
        return f"eip-{eip_number}.md"
        
    def _get_eip_url(self, eip_number: int) -> str:
        """Generate URL for EIP file"""
        filename = self._get_eip_filename(eip_number)
        return f"{self.BASE_URL}/EIPS/{filename}"
        
    def download_eip(self, eip_number: int, verbose: bool = True) -> bool:
        """
        Download a single EIP.
        
        Args:
            eip_number: EIP number (e.g., 1, 20, 721)
            verbose: Print status messages
            
        Returns:
            True if successful, False otherwise
        """
        self._ensure_output_dir()
        
        url = self._get_eip_url(eip_number)
        filename = self._get_eip_filename(eip_number)
        filepath = os.path.join(self.output_dir, filename)
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(response.text)
                
            if verbose:
                print(f"✓ Downloaded EIP-{eip_number} to {filepath}")
            return True
            
        except requests.exceptions.HTTPError as e:
            if response.status_code == 404:
                if verbose:
                    print(f"✗ EIP-{eip_number} not found (404)")
            else:
                if verbose:
                    print(f"✗ Failed to download EIP-{eip_number}: {e}")
            return False
        except requests.exceptions.RequestException as e:
            if verbose:
                print(f"✗ Network error downloading EIP-{eip_number}: {e}")
            return False
        except IOError as e:
            if verbose:
                print(f"✗ Failed to save EIP-{eip_number}: {e}")
            return False
            
    def download_eips(self, eip_numbers: List[int], verbose: bool = True) -> dict:
        """
        Download multiple EIPs.
        
        Args:
            eip_numbers: List of EIP numbers to download
            verbose: Print status messages
            
        Returns:
            Dictionary with 'success' and 'failed' lists
        """
        self._ensure_output_dir()
        
        results = {
            'success': [],
            'failed': []
        }
        
        if verbose:
            print(f"Starting download of {len(eip_numbers)} EIPs to '{self.output_dir}'...")
            print()
        
        for eip_number in eip_numbers:
            if self.download_eip(eip_number, verbose=verbose):
                results['success'].append(eip_number)
            else:
                results['failed'].append(eip_number)
        
        if verbose:
            print()
            print(f"Download complete: {len(results['success'])} succeeded, {len(results['failed'])} failed")
            if results['failed']:
                print(f"Failed EIPs: {results['failed']}")
        
        return results


def main():
    """Command-line interface"""
    if len(sys.argv) < 2:
        print("Usage: python eip_download.py <eip1> <eip2> ... [--output-dir DIR]")
        print("Example: python eip_download.py eip-1 eip-20 eip-721")
        print("Example: python eip_download.py 1 20 721")
        print("Example: python eip_download.py eip-1 eip-20 --output-dir ./my_eips")
        sys.exit(1)
    
    # Parse arguments
    eip_numbers = []
    output_dir = None
    
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == '--output-dir' and i + 1 < len(args):
            output_dir = args[i + 1]
        elif not arg.startswith('--'):
            try:
                # Handle both "eip-7732" and "7732" formats
                eip_str = arg
                if eip_str.lower().startswith('eip-'):
                    eip_str = eip_str[4:]  # Remove "eip-" prefix
                eip_numbers.append(int(eip_str))
            except ValueError:
                print(f"Invalid EIP number: {arg}")
                sys.exit(1)
    
    if not eip_numbers:
        print("No EIP numbers provided")
        sys.exit(1)
    
    # Download EIPs
    downloader = EIPDownloader(output_dir=output_dir)
    results = downloader.download_eips(eip_numbers)
    
    # Exit with error code if any failed
    sys.exit(0 if not results['failed'] else 1)


if __name__ == "__main__":
    main()
