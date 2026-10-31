import httpx
from urllib.parse import quote

# Get the location from the arguments provided by the LLM
location = args.get('location')

if not location:
    result = "Please specify a location to get the weather."
else:
    try:
        # URL-encode the location to handle spaces, slashes, and special characters
        encoded_location = quote(location)
        
        # Use the wttr.in JSON API
        response = httpx.get(f"https://wttr.in/{encoded_location}?format=j1", timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        weather_data = response.json()
        
        # wttr.in sometimes nests the result under a "data" key (e.g., when falling back to lat/lon resolution)
        if 'data' in weather_data and isinstance(weather_data['data'], dict):
            weather_data = weather_data['data']
            
        # Extract relevant information
        current_condition = weather_data.get('current_condition', [{}])[0]
        temp_c = current_condition.get('temp_C')
        weather_desc = current_condition.get('weatherDesc', [{}])[0].get('value')
        feels_like_c = current_condition.get('FeelsLikeC')
        
        if temp_c and weather_desc:
            result = f"The weather in {location} is {temp_c}°C and {weather_desc}. It feels like {feels_like_c}°C."
        else:
            result = "Could not retrieve detailed weather information for that location."
            
    except Exception as e:
        result = f"An error occurred while fetching weather: {str(e)}"