import logging
from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework import status
from datetime import datetime, timedelta
import requests
import json
from openai import OpenAI
import math
from .models import *
from .serializers import *
from .hos_config import DEFAULT_HOS_CONFIG, get_hos_config

logger = logging.getLogger(__name__)


def make_aware_datetime(dt):
    """Ensure a datetime is timezone-aware (uses UTC if naive)"""
    if dt is None:
        return None
    
    # Handle string input - parse it first
    if isinstance(dt, str):
        if dt.endswith('Z'):
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        elif '+' in dt or (dt.count('-') > 2 and 'T' in dt):
            dt = datetime.fromisoformat(dt)
        else:
            dt = datetime.fromisoformat(dt)
    
    # Now ensure it's timezone-aware
    if hasattr(dt, 'tzinfo') and timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def get_dist(p1, p2):
    """Calculate distance between two points (Haversine approximation for short distances)"""
    R = 3958.8  # Miles
    # Handle both dict and tuple inputs
    lat1 = p1['lat'] if isinstance(p1, dict) else p1[0]
    lng1 = p1['lng'] if isinstance(p1, dict) else p1[1]
    lat2 = p2['lat'] if isinstance(p2, dict) else p2[0]
    lng2 = p2['lng'] if isinstance(p2, dict) else p2[1]
    
    dLat = math.radians(lat2 - lat1)
    dLng = math.radians(lng2 - lng1)
    a = (math.sin(dLat / 2) * math.sin(dLat / 2) +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dLng / 2) * math.sin(dLng / 2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_coordinate_at_distance(target_dist, polyline):
    """Find the coordinate at a specific distance along a polyline"""
    if not polyline:
        return {'lat': 0, 'lng': 0}
    if target_dist <= 0:
        return {'lat': polyline[0][0], 'lng': polyline[0][1]}
    
    accumulated_dist = 0
    for i in range(len(polyline) - 1):
        p1 = polyline[i]
        p2 = polyline[i + 1]
        segment_dist = get_dist(p1, p2)
        
        if accumulated_dist + segment_dist >= target_dist:
            remaining = target_dist - accumulated_dist
            ratio = remaining / segment_dist if segment_dist > 0 else 0
            return {
                'lat': p1[0] + (p2[0] - p1[0]) * ratio,
                'lng': p1[1] + (p2[1] - p1[1]) * ratio
            }
        accumulated_dist += segment_dist
    
    last = polyline[-1]
    return {'lat': last[0], 'lng': last[1]}


def round_to_15_minutes(dt):
    """Round datetime to nearest 15-minute interval"""
    minutes = (dt.minute // 15) * 15
    return dt.replace(minute=minutes, second=0, microsecond=0)


def normalize_event_coordinates(events, route_points):
    """
    Normalize event coordinates to ensure waypoint events use correct anchors.
    
    This fixes coordinate drift where events at origin/pickup/dropoff locations
    have incorrect lat/lng due to polyline interpolation errors.
    
    Args:
        events: List of event dictionaries with 'remarks', 'location', 'lat', 'lng'
        route_points: Dict with 'origin', 'pickup', 'dropoff' containing lat/lng
    
    Returns:
        List of events with corrected coordinates
    """
    origin = route_points.get('origin', {})
    pickup = route_points.get('pickup', {})
    dropoff = route_points.get('dropoff', {})
    
    for event in events:
        remarks = event.get('remarks', '')
        location = event.get('location', '')
        
        # Check if event should be anchored to a specific waypoint
        if any(keyword in remarks for keyword in ['Pre-trip', 'Home Terminal', 'Origin']):
            if origin.get('lat') and origin.get('lng'):
                event['lat'] = origin['lat']
                event['lng'] = origin['lng']
        
        elif any(keyword in remarks for keyword in ['Pickup', 'Loading']) and 'Post-trip' not in remarks:
            if pickup.get('lat') and pickup.get('lng'):
                event['lat'] = pickup['lat']
                event['lng'] = pickup['lng']
        
        elif any(keyword in remarks for keyword in ['Dropoff', 'Unloading', 'Post-trip', 'Delivery']):
            if dropoff.get('lat') and dropoff.get('lng'):
                event['lat'] = dropoff['lat']
                event['lng'] = dropoff['lng']
    
    return events


def validate_event_coordinates(events, route_points, tolerance_degrees=0.5):
    """
    Validate that event coordinates match their location labels.
    
    Args:
        events: List of event dictionaries
        route_points: Dict with origin/pickup/dropoff coordinates
        tolerance_degrees: Maximum allowed deviation in degrees (~35 miles at equator)
    
    Returns:
        List of validation warnings
    """
    warnings = []
    origin = route_points.get('origin', {})
    pickup = route_points.get('pickup', {})
    dropoff = route_points.get('dropoff', {})
    
    for i, event in enumerate(events):
        remarks = event.get('remarks', '')
        lat = event.get('lat', 0)
        lng = event.get('lng', 0)
        
        # Check origin events
        if any(keyword in remarks for keyword in ['Pre-trip', 'Home Terminal', 'Origin']):
            if origin.get('lat') and origin.get('lng'):
                dist_lat = abs(lat - origin['lat'])
                dist_lng = abs(lng - origin['lng'])
                if dist_lat > tolerance_degrees or dist_lng > tolerance_degrees:
                    warnings.append({
                        'event_index': i,
                        'remarks': remarks,
                        'issue': 'Origin coordinate mismatch',
                        'expected': (origin['lat'], origin['lng']),
                        'actual': (lat, lng),
                        'deviation_degrees': max(dist_lat, dist_lng)
                    })
        
        # Check pickup events
        elif any(keyword in remarks for keyword in ['Pickup', 'Loading']) and 'Post-trip' not in remarks:
            if pickup.get('lat') and pickup.get('lng'):
                dist_lat = abs(lat - pickup['lat'])
                dist_lng = abs(lng - pickup['lng'])
                if dist_lat > tolerance_degrees or dist_lng > tolerance_degrees:
                    warnings.append({
                        'event_index': i,
                        'remarks': remarks,
                        'issue': 'Pickup coordinate mismatch',
                        'expected': (pickup['lat'], pickup['lng']),
                        'actual': (lat, lng),
                        'deviation_degrees': max(dist_lat, dist_lng)
                    })
        
        # Check dropoff events
        elif any(keyword in remarks for keyword in ['Dropoff', 'Unloading', 'Post-trip', 'Delivery']):
            if dropoff.get('lat') and dropoff.get('lng'):
                dist_lat = abs(lat - dropoff['lat'])
                dist_lng = abs(lng - dropoff['lng'])
                if dist_lat > tolerance_degrees or dist_lng > tolerance_degrees:
                    warnings.append({
                        'event_index': i,
                        'remarks': remarks,
                        'issue': 'Dropoff coordinate mismatch',
                        'expected': (dropoff['lat'], dropoff['lng']),
                        'actual': (lat, lng),
                        'deviation_degrees': max(dist_lat, dist_lng)
                    })
    
    return warnings


def get_route_data(origin, pickup, dropoff):
    """Internal function to get route data using OpenAI"""
    try:
        # Initialize OpenAI client with minimal configuration
        if not hasattr(settings, 'OPENAI_API_KEY') or not settings.OPENAI_API_KEY:
            raise Exception('OpenAI API key not configured')
            
        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=30.0,
            max_retries=2
        )
        
        prompt = f"""
        I need to geocode these locations and create a route plan for trucking:
        
        Origin: {origin}
        Pickup: {pickup} 
        Dropoff: {dropoff}
        
        Please provide:
        1. GPS coordinates (latitude, longitude) for each location
        2. Basic route polyline with key waypoints
        3. Estimated driving distance in miles
        4. Estimated driving time in hours
        5. Turn-by-turn directions
        
        Return as JSON with this structure:
        {{
            "points": {{
                "origin": {{"address": "{origin}", "lat": 0.0, "lng": 0.0}},
                "pickup": {{"address": "{pickup}", "lat": 0.0, "lng": 0.0}},
                "dropoff": {{"address": "{dropoff}", "lat": 0.0, "lng": 0.0}}
            }},
            "distanceMiles": 0.0,
            "durationHours": 0.0,
            "polyline": [[lat1, lng1], [lat2, lng2], ...],
            "instructions": ["instruction1", "instruction2", ...]
        }}
        """
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a trucking route planning assistant. Return only valid JSON. Do not include any explanatory text before or after the JSON."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000,
            temperature=0.3
        )
        
        # Extract and clean the response content
        content = response.choices[0].message.content.strip()
        
        # Remove any markdown code blocks or extra text
        if content.startswith('```'):
            lines = content.split('\n')
            start_idx = 1 if lines[0] == '```json' or lines[0] == '```' else 0
            end_idx = len(lines) - 1 if lines[-1] == '```' else len(lines)
            content = '\n'.join(lines[start_idx:end_idx])
        
        # Find JSON content (looking for first { to last })
        start = content.find('{')
        end = content.rfind('}') + 1
        if start >= 0 and end > start:
            content = content[start:end]
        
        try:
            return json.loads(content)
        except json.JSONDecodeError as json_error:
            logger.warning(f"JSON parsing failed: {json_error}")
            logger.debug(f"Problematic content: {content[:500]}...")
            
            # Return a fallback response
            return {
                "points": {
                    "origin": {"address": origin, "lat": 34.0522, "lng": -118.2437},
                    "pickup": {"address": pickup, "lat": 39.7392, "lng": -104.9903},
                    "dropoff": {"address": dropoff, "lat": 41.8781, "lng": -87.6298}
                },
                "distanceMiles": 1200.0,
                "durationHours": 18.0,
                "polyline": [[34.0522, -118.2437], [39.7392, -104.9903], [41.8781, -87.6298]],
                "instructions": [f"Drive from {origin} to {pickup}", f"Drive from {pickup} to {dropoff}"]
            }
        
    except Exception as e:
        raise Exception(f'Route calculation failed: {str(e)}')


@api_view(['POST'])
@permission_classes([AllowAny])
def geocode_and_route(request):
    """Geocode locations and create route using OpenAI"""
    try:
        data = request.data
        origin = data.get('origin')
        pickup = data.get('pickup')  
        dropoff = data.get('dropoff')
        
        if not all([origin, pickup, dropoff]):
            return Response(
                {'error': 'Origin, pickup, and dropoff locations are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        route_data = get_route_data(origin, pickup, dropoff)
        return Response(route_data, status=status.HTTP_200_OK)
        
    except json.JSONDecodeError:
        return Response(
            {'error': 'Failed to parse route response'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        logger.error(f"Route calculation error: {e}")
        return Response(
            {'error': 'Route calculation failed. Please try again.'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def generate_duty_events(route_data, start_time, hos_config, average_speed=55, target_timezone=None):
    """Generate duty events based on route and HOS rules.
    
    Uses HOUR-BASED approach: all times are stored as decimal hours (0-24) within each day.
    This eliminates timezone conversion issues completely.
    
    Args:
        route_data: Route information
        start_time: Trip start time (string like "2026-02-20T08:00" or "08:00")
        hos_config: Hours of Service configuration
        average_speed: Average driving speed in mph
        target_timezone: Ignored (kept for API compatibility)
    
    Returns:
        List of events with startHour, endHour, startTime, endTime fields
    """
    events = []
    
    # Parse start time to get the starting hour (0-24)
    start_hour = 8.0  # Default to 8 AM
    start_date = None
    
    if isinstance(start_time, str):
        # Handle "2026-02-20T08:00" or "08:00" format
        if 'T' in start_time:
            parts = start_time.split('T')
            start_date = parts[0]  # "2026-02-20"
            time_part = parts[1].split('+')[0].split('-')[0]  # Remove timezone if present
        else:
            time_part = start_time
            start_date = datetime.now().strftime('%Y-%m-%d')
        
        # Parse hours and minutes
        if ':' in time_part:
            time_parts = time_part.split(':')
            hours = int(time_parts[0])
            minutes = int(time_parts[1]) if len(time_parts) > 1 else 0
            start_hour = hours + minutes / 60.0
    elif hasattr(start_time, 'hour'):
        start_hour = start_time.hour + start_time.minute / 60.0
        start_date = start_time.strftime('%Y-%m-%d')
    
    if not start_date:
        start_date = datetime.now().strftime('%Y-%m-%d')
    
    # Round to 15-minute intervals
    start_hour = round(start_hour * 4) / 4
    
    logger.info(f"Generating events starting at hour {start_hour} on {start_date}")
    
    # Current hour tracker (can go beyond 24 for multi-day trips)
    current_hour = 0.0
    current_distance = 0
    
    # Extract waypoint coordinates
    points = route_data.get('points', {})
    origin_coords = {
        'lat': points.get('origin', {}).get('lat', 0),
        'lng': points.get('origin', {}).get('lng', 0),
        'address': points.get('origin', {}).get('address', 'Origin')
    }
    pickup_coords = {
        'lat': points.get('pickup', {}).get('lat', 0),
        'lng': points.get('pickup', {}).get('lng', 0),
        'address': points.get('pickup', {}).get('address', 'Pickup')
    }
    dropoff_coords = {
        'lat': points.get('dropoff', {}).get('lat', 0),
        'lng': points.get('dropoff', {}).get('lng', 0),
        'address': points.get('dropoff', {}).get('address', 'Dropoff')
    }
    
    def hour_to_time_string(hour):
        """Convert decimal hour to HH:MM string, with day indicator if past midnight"""
        day_offset = int(hour // 24)
        h = int(hour) % 24
        m = int((hour % 1) * 60)
        time_str = f"{h:02d}:{m:02d}"
        if day_offset > 0:
            time_str += f" (+{day_offset})"
        return time_str
    
    def add_event(status, duration_hours, location, remarks, use_waypoint=None):
        """Add a duty event with hour-based times"""
        nonlocal current_hour, current_distance
        
        # Round duration to 15-minute intervals
        duration = round(duration_hours * 4) / 4
        if duration <= 0:
            return
        
        end_hour = current_hour + duration
        
        # Determine coordinates
        if use_waypoint == 'origin':
            lat, lng = origin_coords['lat'], origin_coords['lng']
        elif use_waypoint == 'pickup':
            lat, lng = pickup_coords['lat'], pickup_coords['lng']
        elif use_waypoint == 'dropoff':
            lat, lng = dropoff_coords['lat'], dropoff_coords['lng']
        else:
            coords = get_coordinate_at_distance(current_distance, route_data.get('polyline', []))
            lat, lng = coords['lat'], coords['lng']
        
        # Calculate miles
        miles = duration * average_speed if status == 'driving' else 0
        
        events.append({
            'status': status,
            'startHour': round(current_hour, 2),
            'endHour': round(end_hour, 2),
            'startTime': hour_to_time_string(current_hour),
            'endTime': hour_to_time_string(end_hour),
            'location': location,
            'remarks': remarks,
            'lat': lat,
            'lng': lng,
            'miles': round(miles, 2),
            'start': None,  # Legacy field - will be set later if needed
            'end': None,    # Legacy field - will be set later if needed
        })
        
        current_distance += miles
        current_hour = end_hour
    
    # 1. OFF DUTY from midnight (0:00) to start time
    if start_hour > 0:
        add_event("off_duty", start_hour, "Home Terminal", 
                  "Off Duty - Continuous Rest Period", use_waypoint='origin')
    
    # Get HOS configuration
    max_driving = hos_config.get('shiftRules', {}).get('maxDrivingHours', 11)
    max_duty = hos_config.get('shiftRules', {}).get('maxDutyWindowHours', 14)
    break_after = hos_config.get('breakRules', {}).get('requiredAfterDrivingHours', 8)
    break_mins = hos_config.get('breakRules', {}).get('breakDurationMinutes', 30)
    rest_hours = hos_config.get('shiftRules', {}).get('minOffDutyBeforeShiftHours', 10)
    
    # Calculate driving segments
    total_driving = route_data.get('durationHours', 8)
    total_distance = route_data.get('distanceMiles', 100)
    
    # Calculate segment proportions
    origin_to_pickup = get_dist(origin_coords, pickup_coords)
    pickup_to_dropoff = get_dist(pickup_coords, dropoff_coords)
    total_straight = origin_to_pickup + pickup_to_dropoff
    
    if total_straight > 0:
        pickup_ratio = origin_to_pickup / total_straight
    else:
        pickup_ratio = 0.4
    
    drive_to_pickup = total_driving * pickup_ratio
    drive_to_dropoff = total_driving * (1 - pickup_ratio)
    
    # Track shift hours
    shift_driving = 0
    shift_duty = 0
    since_break = 0
    
    # 2. PRE-TRIP INSPECTION
    add_event("on_duty_not_driving", 0.25, origin_coords['address'], 
              "Pre-trip Inspection", use_waypoint='origin')
    shift_duty += 0.25
    
    # 3. DRIVING TO PICKUP
    remaining = drive_to_pickup
    while remaining > 0.01:
        # Check for break
        if since_break >= break_after:
            add_event("off_duty", break_mins / 60, "Rest Area", f"{break_mins}-min Break")
            shift_duty += break_mins / 60
            since_break = 0
            continue
        
        # Calculate max driving segment
        max_segment = min(
            remaining,
            max_driving - shift_driving,
            max_duty - shift_duty,
            break_after - since_break
        )
        
        if max_segment <= 0.01:
            break
        
        add_event("driving", max_segment, "En Route to Pickup", "Driving to Pickup Location")
        remaining -= max_segment
        shift_driving += max_segment
        shift_duty += max_segment
        since_break += max_segment
    
    # 4. PICKUP (LOADING)
    add_event("on_duty_not_driving", 1.0, pickup_coords['address'], 
              "Pickup (Loading)", use_waypoint='pickup')
    shift_duty += 1.0
    
    # 5. DRIVING TO DROPOFF
    remaining = drive_to_dropoff
    while remaining > 0.01:
        # Check driving limit
        if shift_driving >= max_driving or shift_duty >= max_duty:
            add_event("sleeper_berth", rest_hours, "Truck Stop", f"{rest_hours}h Daily Rest")
            shift_driving = 0
            shift_duty = 0
            since_break = 0
            continue
        
        # Check for break
        if since_break >= break_after:
            add_event("off_duty", break_mins / 60, "Rest Area", f"{break_mins}-min Break")
            shift_duty += break_mins / 60
            since_break = 0
            continue
        
        max_segment = min(
            remaining,
            max_driving - shift_driving,
            max_duty - shift_duty,
            break_after - since_break
        )
        
        if max_segment <= 0.01:
            add_event("sleeper_berth", rest_hours, "Rest Area", "Duty Window Rest")
            shift_driving = 0
            shift_duty = 0
            since_break = 0
            continue
        
        add_event("driving", max_segment, "En Route to Dropoff", "Driving to Delivery Location")
        remaining -= max_segment
        shift_driving += max_segment
        shift_duty += max_segment
        since_break += max_segment
    
    # 6. DROPOFF (UNLOADING)
    add_event("on_duty_not_driving", 1.0, dropoff_coords['address'], 
              "Dropoff (Unloading)", use_waypoint='dropoff')
    
    # 7. POST-TRIP INSPECTION (last duty event; nothing is added after step 8)
    add_event("on_duty_not_driving", 0.25, dropoff_coords['address'], 
              "Post-trip Inspection", use_waypoint='dropoff')
    
    # 8. After Post-Trip, always return to Off Duty (required for ELD). No further events after this.
    # current_hour can be >= 24 on multi-day trips (e.g. 46 = 16:00 on day 2); add Off Duty until next midnight.
    if current_hour < 24:
        remaining_hours = 24 - current_hour
    else:
        # End of current "log day": next 24h boundary (e.g. 48 if current_hour is 46)
        next_midnight = math.ceil(current_hour / 24) * 24
        remaining_hours = next_midnight - current_hour
    add_event("off_duty", remaining_hours, "Home Terminal", 
              "Off Duty - End of Day Rest Period", use_waypoint='dropoff')
    
    return events


def split_events_into_days(events, average_speed=55, target_timezone=None, start_date=None):
    """
    Split events into daily logs using HOUR-BASED approach.
    
    Events have startHour/endHour values (0-24+). This function splits them
    at 24-hour boundaries into separate daily logs.
    
    Args:
        events: List of events with startHour, endHour fields
        average_speed: Average driving speed for miles calculation
        target_timezone: Ignored (kept for API compatibility)
        start_date: Starting date string (YYYY-MM-DD), defaults to today
    
    Returns:
        List of daily logs, each containing events with hours 0-24
    """
    if not events:
        return []
    
    # Determine start date
    if not start_date:
        start_date = datetime.now().strftime('%Y-%m-%d')
    
    def hour_to_time_string(hour):
        """Convert decimal hour (0-24) to HH:MM string"""
        h = int(hour) % 24
        m = int((hour % 1) * 60)
        return f"{h:02d}:{m:02d}"
    
    def add_days(date_str, days):
        """Add days to a date string"""
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        new_date = date_obj + timedelta(days=days)
        return new_date.strftime('%Y-%m-%d')
    
    daily_logs = {}
    
    def get_or_create_day(day_index):
        """Get or create a daily log for a specific day offset"""
        date_str = add_days(start_date, day_index)
        if date_str not in daily_logs:
            daily_logs[date_str] = {
                'date': date_str,
                'events': [],
                'totals': {
                    'off_duty': 0,
                    'sleeper_berth': 0,
                    'driving': 0,
                    'on_duty_not_driving': 0,
                },
                'totalMiles': 0
            }
        return date_str
    
    # Process each event
    for event in events:
        start_hour = event.get('startHour', 0)
        end_hour = event.get('endHour', 0)
        
        # Skip invalid events
        if end_hour <= start_hour:
            continue
        
        # Calculate which day(s) this event spans
        start_day = int(start_hour // 24)
        end_day = int((end_hour - 0.001) // 24)  # -0.001 to handle exact 24.0
        
        if start_day == end_day:
            # Event is within a single day
            date_str = get_or_create_day(start_day)
            local_start = start_hour % 24
            local_end = end_hour % 24 if end_hour % 24 != 0 else 24
            
            duration = local_end - local_start
            miles = event.get('miles', 0)
            
            daily_logs[date_str]['events'].append({
                'status': event['status'],
                'startHour': round(local_start, 2),
                'endHour': round(local_end, 2),
                'startTime': hour_to_time_string(local_start),
                'endTime': hour_to_time_string(local_end),
                'location': event['location'],
                'remarks': event['remarks'],
                'lat': event.get('lat'),
                'lng': event.get('lng'),
                'miles': round(miles, 2),
            })
            
            daily_logs[date_str]['totals'][event['status']] += duration
            daily_logs[date_str]['totalMiles'] += miles
        else:
            # Event crosses midnight - split it across multiple days
            # Example: sleeper_berth 23:15 to 09:15 next day (hour 23.25 to 33.25)
            #   Day 0: 23:15-24:00 = 0.75 hours
            #   Day 1: 00:00-09:15 = 9.25 hours
            #   Total: 10 hours
            is_first = True
            total_duration = end_hour - start_hour
            total_miles = event.get('miles', 0)
            
            for day in range(start_day, end_day + 1):
                day_start = day * 24  # e.g., day 0 = 0, day 1 = 24
                day_end = (day + 1) * 24  # e.g., day 0 = 24, day 1 = 48
                
                # Calculate this day's portion using GLOBAL hours
                portion_start = max(start_hour, day_start)
                portion_end = min(end_hour, day_end)
                
                if portion_end <= portion_start:
                    continue
                
                # Convert to LOCAL hours (0-24) for display
                local_start = portion_start % 24
                local_end = portion_end % 24 if portion_end % 24 != 0 else 24
                
                # Duration calculated from GLOBAL hours (this is the actual time)
                duration = portion_end - portion_start
                
                # Calculate proportional miles based on duration ratio
                if total_duration > 0:
                    portion_miles = total_miles * (duration / total_duration)
                else:
                    portion_miles = 0
                
                date_str = get_or_create_day(day)
                
                daily_logs[date_str]['events'].append({
                    'status': event['status'],
                    'startHour': round(local_start, 2),
                    'endHour': round(local_end, 2),
                    'startTime': hour_to_time_string(local_start),
                    'endTime': hour_to_time_string(local_end),
                    'location': event['location'],
                    'remarks': event['remarks'] + (' (continued)' if not is_first else ''),
                    'lat': event.get('lat'),
                    'lng': event.get('lng'),
                    'miles': round(portion_miles, 2),
                })
                
                # Add duration to the correct status total for this day
                daily_logs[date_str]['totals'][event['status']] += duration
                daily_logs[date_str]['totalMiles'] += portion_miles
                
                is_first = False
    
    # Sort events within each day and round totals
    for date_str in daily_logs:
        daily_logs[date_str]['events'].sort(key=lambda e: e['startHour'])
        daily_logs[date_str]['totalMiles'] = round(daily_logs[date_str]['totalMiles'], 1)
        for status in daily_logs[date_str]['totals']:
            daily_logs[date_str]['totals'][status] = round(
                daily_logs[date_str]['totals'][status], 2
            )
    
    # Return sorted by date
    return sorted(daily_logs.values(), key=lambda x: x['date'])


@api_view(['POST'])
@permission_classes([AllowAny])
def calculate_trip(request):
    """Calculate complete trip with route, events, and daily logs"""
    try:
        data = request.data
        inputs = data.get('inputs', {})
        carrier = data.get('carrier', {})
        hos_config = data.get('hosConfig', {})
        save_to_db = data.get('saveToDb', False)  # Optional flag to save
        
        # Get home terminal timezone for ELD compliance (defaults to Eastern Time)
        home_terminal_timezone = data.get('homeTerminalTimezone', 'America/New_York')
        logger.info(f"Using home terminal timezone: {home_terminal_timezone}")
        
        origin = inputs.get('origin')
        pickup = inputs.get('pickup')
        dropoff = inputs.get('dropoff')
        
        if not all([origin, pickup, dropoff]):
            return Response(
                {'error': 'Origin, pickup, and dropoff locations are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get the route data directly
        route_data = get_route_data(origin, pickup, dropoff)
        
        # Use default HOS config if not provided
        if not hos_config:
            hos_config = DEFAULT_HOS_CONFIG
        
        # Extract start date from startTime
        start_time_str = inputs.get('startTime', '')
        if 'T' in start_time_str:
            start_date = start_time_str.split('T')[0]
        else:
            start_date = datetime.now().strftime('%Y-%m-%d')
        
        # Generate duty events (hour-based approach - no timezone complexity)
        average_speed = 55  # mph
        events = generate_duty_events(
            route_data, 
            start_time_str, 
            hos_config,
            average_speed=average_speed
        )
        
        # Split into daily logs using hour boundaries
        logs = split_events_into_days(events, average_speed=average_speed, start_date=start_date)
        
        # Get trip intelligence
        intel = get_trip_intelligence(inputs)
        
        response_data = {
            'route': route_data,
            'events': events,
            'logs': logs,
            'intel': intel,
            'carrier': carrier,
            'timezone': home_terminal_timezone  # Include timezone for frontend display
        }
        
        # Optionally save to database
        if save_to_db:
            saved_trip = save_trip_to_database(
                inputs, carrier, route_data, events, logs, hos_config, intel
            )
            response_data['id'] = saved_trip.id
            response_data['timestamp'] = saved_trip.created_at.isoformat()
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Trip calculation error: {e}")
        return Response(
            {'error': 'Trip calculation failed. Please try again.'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def get_trip_intelligence(inputs):
    """Get AI-powered trip intelligence"""
    try:
        # Initialize OpenAI client with minimal configuration  
        if not hasattr(settings, 'OPENAI_API_KEY') or not settings.OPENAI_API_KEY:
            return "Intelligence service unavailable: API key not configured"
            
        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=30.0,
            max_retries=2
        )
        
        prompt = f"""
        Analyze this trucking trip and provide intelligent insights:
        
        Origin: {inputs.get('origin')}
        Pickup: {inputs.get('pickup')}
        Dropoff: {inputs.get('dropoff')}
        Start Time: {inputs.get('startTime')}
        Current Cycle Hours Used: {inputs.get('cycleHoursUsed', 0)}
        
        Provide brief insights about:
        1. Route efficiency and potential issues
        2. Hours of Service compliance considerations
        3. Weather or traffic recommendations
        4. Fuel planning suggestions
        
        Keep response under 200 words and focus on actionable insights.
        """
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a trucking operations expert providing trip planning insights."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.7
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        return f"Intelligence service unavailable: {str(e)}"


def save_trip_to_database(inputs, carrier, route_data, events, logs, hos_config, intel):
    """Save complete trip data to database"""
    from django.db import transaction
    
    with transaction.atomic():
        # Create or get carrier info
        carrier_obj = None
        if carrier:
            carrier_obj = CarrierInfo.objects.create(
                driver_name=carrier.get('driverName', ''),
                carrier_name=carrier.get('carrierName', ''),
                main_office_address=carrier.get('mainOfficeAddress', ''),
                home_terminal_address=carrier.get('homeTerminalAddress', ''),
                truck_number=carrier.get('truckNumber', ''),
                shipping_docs=carrier.get('shippingDocs', '')
            )
        
        # Parse start time with timezone awareness
        start_time_str = inputs.get('startTime')
        if isinstance(start_time_str, str):
            if start_time_str.endswith('Z'):
                start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            elif '+' in start_time_str or start_time_str.count('-') > 2:
                start_time = datetime.fromisoformat(start_time_str)
            else:
                start_time = datetime.fromisoformat(start_time_str)
                start_time = make_aware_datetime(start_time)
        else:
            start_time = start_time_str or timezone.now()
        
        # Ensure timezone awareness
        start_time = make_aware_datetime(start_time)
        
        # Create trip
        trip = Trip.objects.create(
            origin=inputs.get('origin', ''),
            pickup=inputs.get('pickup', ''),
            dropoff=inputs.get('dropoff', ''),
            cycle_hours_used=inputs.get('cycleHoursUsed', 0),
            start_time=start_time,
            carrier_info=carrier_obj,
            hos_config=hos_config,
            intel=intel
        )
        
        # Save route if exists
        if route_data:
            points = route_data.get('points', {})
            
            # Create location objects
            origin_loc = Location.objects.create(
                address=points.get('origin', {}).get('address', ''),
                latitude=points.get('origin', {}).get('lat', 0),
                longitude=points.get('origin', {}).get('lng', 0)
            )
            pickup_loc = Location.objects.create(
                address=points.get('pickup', {}).get('address', ''),
                latitude=points.get('pickup', {}).get('lat', 0),
                longitude=points.get('pickup', {}).get('lng', 0)
            )
            dropoff_loc = Location.objects.create(
                address=points.get('dropoff', {}).get('address', ''),
                latitude=points.get('dropoff', {}).get('lat', 0),
                longitude=points.get('dropoff', {}).get('lng', 0)
            )
            
            RouteResult.objects.create(
                trip=trip,
                distance_miles=route_data.get('distanceMiles', 0),
                duration_hours=route_data.get('durationHours', 0),
                polyline=route_data.get('polyline', []),
                instructions=route_data.get('instructions', []),
                origin_location=origin_loc,
                pickup_location=pickup_loc,
                dropoff_location=dropoff_loc
            )
        
        # Save duty events
        # Get base date from start_time for hour-based calculations
        base_date = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        
        for event in events:
            # Convert hour-based values to datetime
            start_hour = event.get('startHour', 0)
            end_hour = event.get('endHour', 0)
            
            # Calculate day offset and time from hours
            start_day_offset = int(start_hour // 24)
            start_hour_of_day = start_hour % 24
            start_minutes = int((start_hour_of_day % 1) * 60)
            start_hours = int(start_hour_of_day)
            
            end_day_offset = int(end_hour // 24)
            end_hour_of_day = end_hour % 24
            end_minutes = int((end_hour_of_day % 1) * 60)
            end_hours = int(end_hour_of_day)
            
            # Construct datetime values
            event_start = base_date + timedelta(days=start_day_offset, hours=start_hours, minutes=start_minutes)
            event_end = base_date + timedelta(days=end_day_offset, hours=end_hours, minutes=end_minutes)
            
            # Ensure timezone awareness
            event_start = make_aware_datetime(event_start)
            event_end = make_aware_datetime(event_end)
            
            DutyEvent.objects.create(
                trip=trip,
                status=event['status'],
                start_time=event_start,
                end_time=event_end,
                location=event['location'],
                remarks=event['remarks'],
                latitude=event.get('lat'),
                longitude=event.get('lng'),
                miles=event.get('miles', 0)
            )
        
        # Save daily logs
        for log in logs:
            log_date = datetime.fromisoformat(log['date']).date() if isinstance(log['date'], str) else log['date']
            totals = log.get('totals', {})
            
            DailyLog.objects.create(
                trip=trip,
                date=log_date,
                total_miles=log.get('totalMiles', 0),
                off_duty_hours=totals.get('off_duty', 0),
                sleeper_berth_hours=totals.get('sleeper_berth', 0),
                driving_hours=totals.get('driving', 0),
                on_duty_not_driving_hours=totals.get('on_duty_not_driving', 0)
            )
        
        return trip


@api_view(['GET'])
@permission_classes([AllowAny])
def get_hos_configuration(request):
    """
    Get the HOS configuration profile.
    Returns the complete US FMCSA Property-carrying (Interstate) configuration.
    """
    profile_id = request.GET.get('profileId', None)
    config = get_hos_config(profile_id)
    return Response(config, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """Simple health check endpoint"""
    return Response({'status': 'healthy', 'message': 'ELD API is running'})


# Additional API endpoints for historical data
@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def trips_list(request):
    """
    GET: Get list of all saved trips
    POST: Save a new trip to database
    """
    if request.method == 'GET':
        trips = Trip.objects.all().order_by('-created_at')
        serializer = SavedTripSerializer(trips, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    elif request.method == 'POST':
        try:
            data = request.data
            inputs = data.get('inputs', {})
            carrier = data.get('carrier', {})
            route_data = data.get('route', {})
            events = data.get('events', [])
            logs = data.get('logs', [])
            hos_config = data.get('hosConfig', {})
            intel = data.get('intel', '')
            
            # Save to database
            trip = save_trip_to_database(
                inputs, carrier, route_data, events, logs, hos_config, intel
            )
            
            # Return the saved trip
            serializer = SavedTripSerializer(trip)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Failed to save trip: {e}")
            return Response(
                {'error': 'Failed to save trip. Please try again.'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@api_view(['GET', 'DELETE'])
@permission_classes([AllowAny])
def trip_detail(request, trip_id):
    """
    GET: Get a specific trip by ID
    DELETE: Delete a trip by ID
    """
    try:
        trip = Trip.objects.get(id=trip_id)
        
        if request.method == 'GET':
            serializer = SavedTripSerializer(trip)
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        elif request.method == 'DELETE':
            trip.delete()
            return Response(
                {'message': 'Trip deleted successfully'}, 
                status=status.HTTP_204_NO_CONTENT
            )
            
    except Trip.DoesNotExist:
        return Response(
            {'error': 'Trip not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )


@api_view(['GET'])
@permission_classes([AllowAny])
def get_daily_logs(request, trip_id):
    """Get daily logs for a specific trip"""
    try:
        trip = Trip.objects.get(id=trip_id)
        logs = trip.daily_logs.all()
        serializer = DailyLogSerializer(logs, many=True)
        return Response(serializer.data)
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([AllowAny])
def fix_trip_coordinates(request, trip_id):
    """
    Fix/normalize coordinates for an existing trip's duty events.
    
    This is useful for correcting trips that were saved with incorrect
    coordinates due to polyline interpolation drift.
    """
    try:
        trip = Trip.objects.get(id=trip_id)
        
        # Get route points - use related_name 'route' not 'route_result'
        try:
            route_result = trip.route
        except RouteResult.DoesNotExist:
            route_result = None
            
        if not route_result:
            return Response(
                {'error': 'Trip has no route data'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        route_points = {
            'origin': {
                'lat': route_result.origin_location.latitude,
                'lng': route_result.origin_location.longitude
            },
            'pickup': {
                'lat': route_result.pickup_location.latitude,
                'lng': route_result.pickup_location.longitude
            },
            'dropoff': {
                'lat': route_result.dropoff_location.latitude,
                'lng': route_result.dropoff_location.longitude
            }
        }
        
        # Get all events
        events = trip.duty_events.all().order_by('start_time')
        
        # Validate before fix
        events_data = [{
            'remarks': e.remarks,
            'location': e.location,
            'lat': e.latitude,
            'lng': e.longitude
        } for e in events]
        
        warnings_before = validate_event_coordinates(events_data, route_points)
        
        # Fix coordinates
        fixed_count = 0
        for event in events:
            remarks = event.remarks
            
            # Check if event should be anchored to a waypoint
            if any(keyword in remarks for keyword in ['Pre-trip', 'Home Terminal', 'Origin']):
                event.latitude = route_points['origin']['lat']
                event.longitude = route_points['origin']['lng']
                event.save()
                fixed_count += 1
            
            elif any(keyword in remarks for keyword in ['Pickup', 'Loading']) and 'Post-trip' not in remarks:
                event.latitude = route_points['pickup']['lat']
                event.longitude = route_points['pickup']['lng']
                event.save()
                fixed_count += 1
            
            elif any(keyword in remarks for keyword in ['Dropoff', 'Unloading', 'Post-trip', 'Delivery']):
                event.latitude = route_points['dropoff']['lat']
                event.longitude = route_points['dropoff']['lng']
                event.save()
                fixed_count += 1
        
        # Validate after fix
        events_data_after = [{
            'remarks': e.remarks,
            'location': e.location,
            'lat': e.latitude,
            'lng': e.longitude
        } for e in trip.duty_events.all().order_by('start_time')]
        
        warnings_after = validate_event_coordinates(events_data_after, route_points)
        
        return Response({
            'message': f'Fixed {fixed_count} event coordinates',
            'warnings_before': len(warnings_before),
            'warnings_after': len(warnings_after),
            'remaining_issues': warnings_after
        }, status=status.HTTP_200_OK)
        
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Failed to fix coordinates for trip {trip_id}: {e}")
        return Response(
            {'error': 'Failed to fix coordinates. Please try again.'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )