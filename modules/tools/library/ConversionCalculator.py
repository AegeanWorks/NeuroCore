value = args.get('value')
from_unit = str(args.get('from_unit')).lower().strip()
to_unit = str(args.get('to_unit')).lower().strip()

try:
    val = float(value)
    res = None
    
    # Temperature
    if from_unit in ['c', 'celsius'] and to_unit in ['f', 'fahrenheit']:
        res = (val * 9/5) + 32
    elif from_unit in ['f', 'fahrenheit'] and to_unit in ['c', 'celsius']:
        res = (val - 32) * 5/9
        
    # Length (Base: meters)
    length_factors = {
        'm': 1, 'meter': 1, 'meters': 1,
        'km': 1000, 'kilometer': 1000, 'kilometers': 1000,
        'cm': 0.01, 'centimeter': 0.01, 'centimeters': 0.01,
        'mm': 0.001, 'millimeter': 0.001, 'millimeters': 0.001,
        'mi': 1609.34, 'mile': 1609.34, 'miles': 1609.34,
        'yd': 0.9144, 'yard': 0.9144, 'yards': 0.9144,
        'ft': 0.3048, 'foot': 0.3048, 'feet': 0.3048,
        'in': 0.0254, 'inch': 0.0254, 'inches': 0.0254
    }

    # Weight (Base: kg)
    weight_factors = {
        'kg': 1, 'kilogram': 1, 'kilograms': 1,
        'g': 0.001, 'gram': 0.001, 'grams': 0.001,
        'mg': 0.000001, 'milligram': 0.000001, 'milligrams': 0.000001,
        'lb': 0.453592, 'lbs': 0.453592, 'pound': 0.453592, 'pounds': 0.453592,
        'oz': 0.0283495, 'ounce': 0.0283495, 'ounces': 0.0283495
    }

    # Volume (Base: liter)
    volume_factors = {
        'l': 1, 'liter': 1, 'liters': 1,
        'ml': 0.001, 'milliliter': 0.001, 'milliliters': 0.001,
        'gal': 3.78541, 'gallon': 3.78541, 'gallons': 3.78541,
        'qt': 0.946353, 'quart': 0.946353, 'quarts': 0.946353,
        'pt': 0.473176, 'pint': 0.473176, 'pints': 0.473176,
        'cup': 0.236588, 'cups': 0.236588,
        'fl oz': 0.0295735, 'fluid ounce': 0.0295735, 'fluid ounces': 0.0295735
    }

    if res is None:
        if from_unit in length_factors and to_unit in length_factors:
            res = val * length_factors[from_unit] / length_factors[to_unit]
        elif from_unit in weight_factors and to_unit in weight_factors:
            res = val * weight_factors[from_unit] / weight_factors[to_unit]
        elif from_unit in volume_factors and to_unit in volume_factors:
            res = val * volume_factors[from_unit] / volume_factors[to_unit]
    
    if res is not None:
        # Format to 4 decimal places, stripping trailing zeros
        formatted_res = f"{res:.4f}".rstrip('0').rstrip('.')
        result = f"{val} {from_unit} is approximately {formatted_res} {to_unit}."
    else:
        result = f"Unsupported conversion: {from_unit} to {to_unit}."

except ValueError:
    result = "Invalid value provided. Please provide a number."
except Exception as e:
    result = f"Error calculating conversion: {str(e)}"