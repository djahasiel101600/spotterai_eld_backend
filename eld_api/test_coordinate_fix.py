"""
Test script to validate event coordinate fixes for map marker ordering.

This tests that events anchored to origin/pickup/dropoff waypoints
have the correct coordinates and appear in proper geographic sequence.
"""

from datetime import datetime
from views import generate_duty_events, validate_event_coordinates, normalize_event_coordinates
from hos_config import DEFAULT_HOS_CONFIG


def test_coordinate_accuracy():
    """Test that waypoint events have correct coordinates"""
    
    # Sample route data (LA -> Denver -> Chicago)
    route_data = {
        'distanceMiles': 2100,
        'durationHours': 32,
        'polyline': [
            [34.0522, -118.2437],  # Los Angeles, CA
            [36.7783, -119.4179],  # Midpoint CA
            [39.7392, -104.9903],  # Denver, CO  
            [41.2524, -95.9980],   # Omaha, NE
            [41.8781, -87.6298]    # Chicago, IL
        ],
        'points': {
            'origin': {
                'address': 'Los Angeles, CA',
                'lat': 34.0522,
                'lng': -118.2437
            },
            'pickup': {
                'address': 'Denver, CO',
                'lat': 39.7392,
                'lng': -104.9903
            },
            'dropoff': {
                'address': 'Chicago, IL',
                'lat': 41.8781,
                'lng': -87.6298
            }
        }
    }
    
    start_time = '2026-02-20T08:00:00'
    
    # Generate events
    events = generate_duty_events(route_data, start_time, DEFAULT_HOS_CONFIG)
    
    # Validate coordinates
    warnings = validate_event_coordinates(events, route_data['points'])
    
    print("=" * 80)
    print("EVENT COORDINATE VALIDATION TEST")
    print("=" * 80)
    print(f"\nTotal events generated: {len(events)}")
    print(f"Validation warnings: {len(warnings)}")
    
    if warnings:
        print("\n⚠️  COORDINATE MISMATCHES FOUND:")
        for warning in warnings:
            print(f"\n  Event #{warning['event_index']}: {warning['remarks']}")
            print(f"  Issue: {warning['issue']}")
            print(f"  Expected: {warning['expected']}")
            print(f"  Actual: {warning['actual']}")
            print(f"  Deviation: {warning['deviation_degrees']:.4f} degrees (~{warning['deviation_degrees'] * 69:.1f} miles)")
    else:
        print("\n✅ All waypoint coordinates are accurate!")
    
    # Check geographic progression
    print("\n" + "=" * 80)
    print("GEOGRAPHIC PROGRESSION CHECK")
    print("=" * 80)
    
    key_events = []
    for i, event in enumerate(events):
        remarks = event.get('remarks', '')
        if any(keyword in remarks for keyword in ['Pre-trip', 'Pickup', 'Dropoff', 'Post-trip', '30-min Break', 'Sleeper', 'Rest']):
            key_events.append({
                'index': i,
                'time': event['start'].strftime('%Y-%m-%d %H:%M'),
                'remarks': remarks,
                'location': event.get('location', ''),
                'lat': event.get('lat', 0),
                'lng': event.get('lng', 0)
            })
    
    print(f"\nKey events (showing waypoints and stops):")
    print(f"{'#':<4} {'Time':<17} {'Location':<25} {'Lat':<10} {'Lng':<11} {'Remarks'}")
    print("-" * 100)
    
    for evt in key_events:
        print(f"{evt['index']:<4} {evt['time']:<17} {evt['location'][:25]:<25} "
              f"{evt['lat']:<10.4f} {evt['lng']:<11.4f} {evt['remarks'][:40]}")
    
    # Check longitude progression (west to east: LA -> Denver -> Chicago)
    # LA: -118.24, Denver: -104.99, Chicago: -87.63
    print("\n" + "=" * 80)
    print("LONGITUDE PROGRESSION (West → East)")
    print("=" * 80)
    
    origin_events = [e for e in key_events if 'Pre-trip' in e['remarks'] or 'Origin' in e['location']]
    pickup_events = [e for e in key_events if 'Pickup' in e['remarks']]
    dropoff_events = [e for e in key_events if 'Dropoff' in e['remarks'] or 'Post-trip' in e['remarks']]
    
    if origin_events:
        print(f"Origin (LA):    {origin_events[0]['lng']:.4f} (expected: -118.2437)")
        origin_ok = abs(origin_events[0]['lng'] - (-118.2437)) < 0.5
        print(f"  {'✅ CORRECT' if origin_ok else '❌ INCORRECT'}")
    
    if pickup_events:
        print(f"Pickup (Denver): {pickup_events[0]['lng']:.4f} (expected: -104.9903)")
        pickup_ok = abs(pickup_events[0]['lng'] - (-104.9903)) < 0.5
        print(f"  {'✅ CORRECT' if pickup_ok else '❌ INCORRECT'}")
    
    if dropoff_events:
        print(f"Dropoff (Chicago): {dropoff_events[0]['lng']:.4f} (expected: -87.6298)")
        dropoff_ok = abs(dropoff_events[0]['lng'] - (-87.6298)) < 0.5
        print(f"  {'✅ CORRECT' if dropoff_ok else '❌ INCORRECT'}")
    
    # Check that longitude increases (moves east)
    if origin_events and pickup_events and dropoff_events:
        origin_lng = origin_events[0]['lng']
        pickup_lng = pickup_events[0]['lng']
        dropoff_lng = dropoff_events[0]['lng']
        
        progression_ok = origin_lng < pickup_lng < dropoff_lng
        print(f"\nProgression check: {origin_lng:.2f} < {pickup_lng:.2f} < {dropoff_lng:.2f}")
        print(f"  {'✅ CORRECT GEOGRAPHIC ORDER' if progression_ok else '❌ OUT OF ORDER'}")
    
    # Test normalization function
    print("\n" + "=" * 80)
    print("TESTING NORMALIZATION FUNCTION")
    print("=" * 80)
    
    # Simulate corrupted coordinates
    test_events = [
        {'remarks': 'Pre-trip Inspection', 'lat': 0, 'lng': 0, 'location': 'Origin'},
        {'remarks': 'Pickup (Loading)', 'lat': 34.05, 'lng': -118.24, 'location': 'Denver'},
        {'remarks': 'Dropoff (Unloading)', 'lat': 39.74, 'lng': -104.99, 'location': 'Chicago'}
    ]
    
    print("\nBefore normalization:")
    for i, evt in enumerate(test_events):
        print(f"  {i+1}. {evt['remarks']}: ({evt['lat']:.4f}, {evt['lng']:.4f})")
    
    normalized = normalize_event_coordinates(test_events, route_data['points'])
    
    print("\nAfter normalization:")
    for i, evt in enumerate(normalized):
        print(f"  {i+1}. {evt['remarks']}: ({evt['lat']:.4f}, {evt['lng']:.4f})")
    
    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)


if __name__ == '__main__':
    test_coordinate_accuracy()
