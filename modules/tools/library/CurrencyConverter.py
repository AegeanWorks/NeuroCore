import httpx
import re

# ISO 4217 currency codes (common ones)
VALID_CURRENCY_CODES = {
    'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD', 'CNY', 'HKD',
    'SGD', 'SEK', 'NOK', 'DKK', 'KRW', 'INR', 'RUB', 'BRL', 'ZAR', 'MXN',
    'AED', 'AFN', 'ALL', 'AMD', 'ANG', 'AOA', 'ARS', 'AWG', 'AZN', 'BAM',
    'BBD', 'BDT', 'BGN', 'BHD', 'BIF', 'BMD', 'BND', 'BOB', 'BSD', 'BTN',
    'BWP', 'BYN', 'BZD', 'CDF', 'CLP', 'COP', 'CRC', 'CUP', 'CVE', 'CZK',
    'DJF', 'DOP', 'DZD', 'EGP', 'ERN', 'ETB', 'FJD', 'FKP', 'GEL', 'GGP',
    'GHS', 'GIP', 'GMD', 'GNF', 'GTQ', 'GYD', 'HNL', 'HRK', 'HTG', 'HUF',
    'IDR', 'ILS', 'IMP', 'IQD', 'IRR', 'ISK', 'JEP', 'JMD', 'JOD', 'KES',
    'KGS', 'KHR', 'KMF', 'KPW', 'KWD', 'KYD', 'KZT', 'LAK', 'LBP', 'LKR',
    'LRD', 'LSL', 'LYD', 'MAD', 'MDL', 'MGA', 'MKD', 'MMK', 'MNT', 'MOP',
    'MRU', 'MUR', 'MVR', 'MWK', 'MYR', 'MZN', 'NAD', 'NGN', 'NIO', 'NPR',
    'OMR', 'PAB', 'PEN', 'PGK', 'PHP', 'PKR', 'PLN', 'PYG', 'QAR', 'RON',
    'RSD', 'RWF', 'SAR', 'SBD', 'SCR', 'SDG', 'SHP', 'SLL', 'SOS', 'SRD',
    'SSP', 'STN', 'SYP', 'SZL', 'THB', 'TJS', 'TMT', 'TND', 'TOP', 'TRY',
    'TTD', 'TWD', 'TZS', 'UAH', 'UGX', 'UYU', 'UZS', 'VES', 'VND', 'VUV',
    'WST', 'XAF', 'XCD', 'XOF', 'XPF', 'YER', 'ZMW', 'ZWL'
}

amount_input = args.get('amount')
from_curr = args.get('from_currency', '').upper().strip()
to_curr = args.get('to_currency', '').upper().strip()

# Input validation
if not amount_input or not from_curr or not to_curr:
    result = "Error: Missing arguments. Requires 'amount', 'from_currency', and 'to_currency'."
else:
    # Validate amount is a positive number
    try:
        amount = float(amount_input)
        if amount <= 0:
            result = "Error: Amount must be a positive number."
        elif amount > 1e15:
            result = "Error: Amount is too large. Maximum allowed is 1,000,000,000,000,000."
        else:
            amount = amount  # Valid amount
    except (ValueError, TypeError):
        result = f"Error: Invalid amount '{amount_input}'. Must be a valid number."
    else:
        # Validate currency codes
        if len(from_curr) != 3 or not from_curr.isalpha():
            result = f"Error: Invalid currency code '{from_curr}'. Must be a 3-letter ISO 4217 code."
        elif from_curr not in VALID_CURRENCY_CODES:
            result = f"Error: Currency code '{from_curr}' is not a recognized ISO 4217 code."
        elif len(to_curr) != 3 or not to_curr.isalpha():
            result = f"Error: Invalid currency code '{to_curr}'. Must be a 3-letter ISO 4217 code."
        elif to_curr not in VALID_CURRENCY_CODES:
            result = f"Error: Currency code '{to_curr}' is not a recognized ISO 4217 code."
        elif from_curr == to_curr:
            result = f"{amount} {from_curr} is {amount} {to_curr}"
        else:
            try:
                # Using frankfurter.app (free, no key required)
                url = f"https://api.frankfurter.app/latest?amount={amount}&from={from_curr}&to={to_curr}"
                
                resp = httpx.get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                
                converted_value = data.get('rates', {}).get(to_curr)
                
                if converted_value is not None:
                    result = f"{amount} {from_curr} = {converted_value} {to_curr} (Date: {data.get('date')})"
                else:
                    result = f"Error: Could not find rate for {to_curr}."
                    
            except httpx.HTTPStatusError as e:
                result = f"HTTP error: {e.response.status_code} - Invalid currency code or API error."
            except Exception as e:
                result = f"Currency conversion failed: {str(e)}"
