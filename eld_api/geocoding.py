# geocoding.py (optimized version)
"""
Optimized geocoding service with caching and batch operations
"""
import requests
from typing import Dict, Optional, List, Tuple
from math import radians, sin, cos, sqrt, atan2
from django.conf import settings
from django.core.cache import cache
from functools import lru_cache
import logging
from timezonefinder import TimezoneFinder

logger = logging.getLogger(__name__)

# Constants
EARTH_RADIUS_MILES = 3958.8
CACHE_TTL = 3600  # 1 hour
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
USER_AGENT = "ELD-Trucking-App/1.0"

# Initialize TimezoneFinder once
_tz_finder = TimezoneFinder()


class GeocodingService:
    """Multi-provider geocoding service with caching"""
    
    @staticmethod
    def geocode_address(address: str) -> Optional[Dict]:
        """Geocode an address with caching"""
        cache_key = f"geocode_{hash(address)}"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            return cached_result
        
        try:
            result = GeocodingService._geocode_address_impl(address)
            if result:
                cache.set(cache_key, result, CACHE_TTL)
            return result
        except Exception as e:
            logger.error(f"Geocoding error for '{address}': {e}")
            return None
    
    @staticmethod
    def _geocode_address_impl(address: str) -> Optional[Dict]:
        """Implementation of geocoding"""
        params = {
            "q": address,
            "format": "json",
            "limit": 1,
            "addressdetails": 1
        }
        headers = {"User-Agent": USER_AGENT}
        
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if not data:
            return None
        
        result = data[0]
        lat = float(result["lat"])
        lng = float(result["lon"])
        
        # Detect timezone
        timezone_name = _tz_finder.timezone_at(lat=lat, lng=lng)
        
        return {
            "lat": lat,
            "lng": lng,
            "address": result["display_name"],
            "bounding_box": result.get("boundingbox"),
            "timezone": timezone_name or "UTC"
        }
    
    @staticmethod
    def batch_geocode(addresses: List[str]) -> List[Optional[Dict]]:
        """Batch geocode multiple addresses"""
        # Try cache first
        results = []
        uncached_indices = []
        uncached_addresses = []
        
        for i, address in enumerate(addresses):
            cache_key = f"geocode_{hash(address)}"
            cached = cache.get(cache_key)
            if cached:
                results.append((i, cached))
            else:
                uncached_indices.append(i)
                uncached_addresses.append(address)
        
        # Geocode uncached addresses
        if uncached_addresses:
            uncached_results = GeocodingService._batch_geocode_impl(uncached_addresses)
            
            # Cache results
            for idx, (orig_idx, result) in enumerate(zip(uncached_indices, uncached_results)):
                if result:
                    cache_key = f"geocode_{hash(addresses[orig_idx])}"
                    cache.set(cache_key, result, CACHE_TTL)
                results.append((orig_idx, result))
        
        # Sort by original index
        results.sort(key=lambda x: x[0])
        return [r[1] for r in results]
    
    @staticmethod
    def _batch_geocode_impl(addresses: List[str]) -> List[Optional[Dict]]:
        """Batch geocode implementation"""
        results = []
        for address in addresses:
            try:
                result = GeocodingService._geocode_address_impl(address)
                results.append(result)
            except Exception as e:
                logger.error(f"Batch geocode error for '{address}': {e}")
                results.append(None)
        return results
    
    @staticmethod
    def calculate_route_distance(origin: Dict, pickup: Dict, dropoff: Dict) -> Dict:
        """Calculate route using OSRM with caching"""
        cache_key = f"route_{hash((origin['lat'], origin['lng'], pickup['lat'], pickup['lng'], dropoff['lat'], dropoff['lng']))}"
        cached = cache.get(cache_key)
        
        if cached:
            return cached
        
        try:
            result = GeocodingService._calculate_route_impl(origin, pickup, dropoff)
            cache.set(cache_key, result, CACHE_TTL)
            return result
        except Exception as e:
            logger.error(f"Route calculation error: {e}")
            result = GeocodingService._fallback_route(origin, pickup, dropoff)
            cache.set(cache_key, result, 300)  # Shorter cache for fallback
            return result
    
    @staticmethod
    def _calculate_route_impl(origin: Dict, pickup: Dict, dropoff: Dict) -> Dict:
        """OSRM route calculation implementation"""
        coords = f"{origin['lng']},{origin['lat']};{pickup['lng']},{pickup['lat']};{dropoff['lng']},{dropoff['lat']}"
        
        params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "true"
        }
        
        response = requests.get(f"{OSRM_URL}/{coords}", params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        if data["code"] != "Ok":
            raise Exception(f"OSRM error: {data.get('message', 'Unknown error')}")
        
        route = data["routes"][0]
        
        # Convert units
        distance_miles = route["distance"] * 0.000621371
        duration_hours = route["duration"] / 3600.0
        
        # Extract polyline
        geometry = route["geometry"]["coordinates"]
        polyline = [[coord[1], coord[0]] for coord in geometry]
        
        # Extract instructions
        instructions = []
        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                instruction = step.get("maneuver", {}).get("instruction", "")
                if instruction:
                    instructions.append(instruction)
        
        return {
            "distance_miles": round(distance_miles, 1),
            "duration_hours": round(duration_hours, 2),
            "polyline": polyline,
            "instructions": instructions or ["Drive to destination"]
        }
    
    @staticmethod
    def _fallback_route(origin: Dict, pickup: Dict, dropoff: Dict) -> Dict:
        """Haversine-based fallback route"""
        
        @lru_cache(maxsize=128)
        def haversine(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
            lat1, lon1 = p1
            lat2, lon2 = p2
            
            dlat = radians(lat2 - lat1)
            dlon = radians(lon2 - lon1)
            a = (sin(dlat/2)**2 + 
                 cos(radians(lat1)) * cos(radians(lat2)) * 
                 sin(dlon/2)**2)
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            return EARTH_RADIUS_MILES * c
        
        p1 = (origin['lat'], origin['lng'])
        p2 = (pickup['lat'], pickup['lng'])
        p3 = (dropoff['lat'], dropoff['lng'])
        
        dist1 = haversine(p1, p2)
        dist2 = haversine(p2, p3)
        total_miles = dist1 + dist2
        duration_hours = total_miles / 55.0
        
        polyline = [
            [origin['lat'], origin['lng']],
            [pickup['lat'], pickup['lng']],
            [dropoff['lat'], dropoff['lng']]
        ]
        
        return {
            "distance_miles": round(total_miles, 1),
            "duration_hours": round(duration_hours, 2),
            "polyline": polyline,
            "instructions": [
                "Drive from origin to pickup location",
                "Drive from pickup to dropoff location"
            ]
        }


def validate_coordinates(lat: float, lng: float) -> bool:
    """Validate coordinates with bounds checking"""
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return False
    
    allow_international = getattr(settings, 'ALLOW_INTERNATIONAL_ROUTES', False)
    
    if allow_international:
        return True
    
    # Continental US + Canada + Mexico bounds
    return (24.0 <= lat <= 71.0) and (-170.0 <= lng <= -50.0)


def validate_coordinates_batch(coords_list: List[Tuple[float, float]]) -> List[bool]:
    """Batch validate coordinates"""
    allow_international = getattr(settings, 'ALLOW_INTERNATIONAL_ROUTES', False)
    
    if allow_international:
        return [(-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0) 
                for lat, lng in coords_list]
    
    return [(24.0 <= lat <= 71.0 and -170.0 <= lng <= -50.0) 
            for lat, lng in coords_list]


def enhance_route_instructions_with_ai(
    basic_instructions: List[str],
    route_metadata: Dict,
    origin_address: str,
    destination_address: str
) -> Dict:
    """
    Enhance route instructions with AI (non-critical)
    
    Returns dict with enhanced_instructions, basic_instructions, and source
    """
    # Early return for empty instructions
    if not basic_instructions:
        return {
            "enhanced_instructions": ["Route instructions unavailable"],
            "basic_instructions": [],
            "source": "fallback_empty"
        }
    
    try:
        from openai import OpenAI
        import re
        
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            logger.debug("OpenAI API key not configured, using fallback")
            return _get_fallback_instructions(basic_instructions)
        
        # Sanitize and truncate inputs to prevent token overflow
        safe_origin = (origin_address or "Unknown origin")[:100]
        safe_destination = (destination_address or "Unknown destination")[:100]
        
        # Get route metadata with safe defaults
        distance = route_metadata.get('distance_miles', 0)
        duration = route_metadata.get('duration_hours', 0)
        
        # Prepare instructions summary (limit to prevent huge prompts)
        instr_count = min(len(basic_instructions), 15)  # Increased from 10 for better context
        instr_summary = '\n'.join(
            f"{i+1}. {instr[:150]}" + ("..." if len(instr) > 150 else "")
            for i, instr in enumerate(basic_instructions[:instr_count])
        )
        
        route_summary = f"""ROUTE INFORMATION:
• Origin: {safe_origin}
• Destination: {safe_destination}
• Distance: {distance:.1f} miles
• Duration: {duration:.1f} hours
• Total turns: {len(basic_instructions)}

TURN-BY-TURN DIRECTIONS:
{instr_summary}
"""
        
        # More structured prompt for better AI responses
        prompt = f"""You are a trucking route expert. Enhance these driving directions with practical trucking knowledge.

{route_summary}

Based on the route information above, provide enhanced directions that include:

1. REST/FUEL STOPS: Suggest optimal locations based on distance (every 4-5 hours driving)
2. HOS COMPLIANCE: Note when 8-hour driving limit approaches
3. TRUCK-SPECIFIC: Mention weigh stations, truck routes, low bridges, or restricted roads
4. TERRAIN NOTES: Highlight steep grades, mountain passes, or construction zones
5. SAFETY TIPS: Weather considerations, high-traffic areas, or alternative routes

Format your response as a numbered list of enhanced instructions that follow the original route order but add this practical guidance. Keep each instruction clear and actionable.

ENHANCED INSTRUCTIONS:"""
        
        # Initialize client with better timeout and error handling
        client = OpenAI(
            api_key=api_key,
            timeout=8.0,  # Slightly longer but still reasonable
            max_retries=1
        )
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system", 
                    "content": "You are a trucking route expert specializing in commercial vehicle operations and Hours of Service compliance."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,  # Increased for more detailed instructions
            temperature=0.7,
            presence_penalty=0.1,  # Slight penalty to reduce repetition
            frequency_penalty=0.1
        )
        
        # Extract and clean response
        content = response.choices[0].message.content.strip()
        
        # Parse response into clean instructions
        enhanced = []
        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue
                
            # Skip common header lines
            if any(line.lower().startswith(x) for x in [
                'here are', 'enhanced', 'based on', 'route information',
                'turn-by-turn', 'directions:', 'instructions:'
            ]):
                continue
            
            # Remove markdown, numbering, and clean up
            line = re.sub(r'^#{1,3}\s+', '', line)  # Remove markdown headers
            line = re.sub(r'^\d+[\.\)]\s*', '', line)  # Remove numbering
            line = re.sub(r'^-\s+', '', line)  # Remove bullet points
            line = re.sub(r'^\*\s+', '', line)  # Remove asterisk bullets
            line = re.sub(r'\s+', ' ', line)  # Normalize whitespace
            
            # Filter out very short or unhelpful lines
            if len(line) > 15 and not line.lower() in [
                'enhanced instructions', 'instructions', 'note:', 'tips:',
                'rest stops:', 'fuel stops:', 'safety tips:'
            ]:
                # Capitalize first letter if needed
                if line and line[0].islower():
                    line = line[0].upper() + line[1:]
                enhanced.append(line)
        
        # If parsing resulted in no instructions, use fallback with reason
        if not enhanced:
            logger.warning("AI returned no parseable instructions")
            return {
                "enhanced_instructions": basic_instructions,
                "basic_instructions": basic_instructions,
                "source": "osrm_only_no_ai_parse"
            }
        
        return {
            "enhanced_instructions": enhanced,
            "basic_instructions": basic_instructions,
            "source": "osrm_with_ai_enhancement"
        }
        
    except ImportError:
        logger.error("OpenAI package not installed")
        return _get_fallback_instructions(basic_instructions)
        
    except Exception as e:
        # Log detailed error but don't expose to user
        error_type = type(e).__name__
        error_msg = str(e)
        
        # Handle specific error types
        if "timeout" in error_msg.lower():
            logger.warning(f"OpenAI timeout after 8s - {error_type}")
        elif "rate limit" in error_msg.lower():
            logger.warning(f"OpenAI rate limit hit - {error_type}")
        elif "authentication" in error_msg.lower():
            logger.error(f"OpenAI auth failed - check API key")
        else:
            logger.error(f"AI enhancement failed - {error_type}: {error_msg[:100]}")
        
        return _get_fallback_instructions(basic_instructions)


def _get_fallback_instructions(basic_instructions: List[str]) -> Dict:
    """
    Enhanced fallback with better formatting
    """
    import re
    
    if not basic_instructions:
        return {
            "enhanced_instructions": ["Route information unavailable"],
            "basic_instructions": [],
            "source": "fallback_empty"
        }
    
    # Clean and format basic instructions for consistency
    cleaned_instructions = []
    for i, instr in enumerate(basic_instructions, 1):
        # Clean up any existing numbers
        clean = re.sub(r'^\d+[\.\)]\s*', '', instr)
        # Capitalize first letter
        if clean and clean[0].islower():
            clean = clean[0].upper() + clean[1:]
        cleaned_instructions.append(clean)
    
    return {
        "enhanced_instructions": cleaned_instructions,
        "basic_instructions": basic_instructions,
        "source": "osrm_only"
    }