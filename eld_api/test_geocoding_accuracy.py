"""
Test suite for geocoding accuracy
Run with: python manage.py shell < eld_api/test_geocoding_accuracy.py
Or: python -c "from eld_api.test_geocoding_accuracy import run_all_tests; run_all_tests()"
"""
from .geocoding import GeocodingService, validate_coordinates


def test_geocoding_accuracy():
    """Test geocoding with known locations"""
    
    test_cases = [
        {
            "address": "Los Angeles, CA",
            "expected_lat_range": (33.5, 34.5),
            "expected_lng_range": (-118.7, -117.7)
        },
        {
            "address": "Denver, CO",
            "expected_lat_range": (39.0, 40.0),
            "expected_lng_range": (-105.5, -104.5)
        },
        {
            "address": "Chicago, IL",
            "expected_lat_range": (41.5, 42.0),
            "expected_lng_range": (-88.0, -87.0)
        },
        {
            "address": "New York, NY",
            "expected_lat_range": (40.5, 41.0),
            "expected_lng_range": (-74.5, -73.5)
        },
        {
            "address": "Miami, FL",
            "expected_lat_range": (25.5, 26.0),
            "expected_lng_range": (-80.5, -80.0)
        }
    ]
    
    print("=" * 80)
    print("GEOCODING ACCURACY TEST")
    print("=" * 80)
    
    passed = 0
    for i, test in enumerate(test_cases, 1):
        print(f"\nTest {i}: {test['address']}")
        
        result = GeocodingService.geocode_address(test['address'])
        
        if not result:
            print(f"  ❌ FAILED: Could not geocode")
            continue
        
        lat, lng = result['lat'], result['lng']
        print(f"  Coordinates: ({lat:.4f}, {lng:.4f})")
        print(f"  Full address: {result['address']}")
        
        lat_ok = test['expected_lat_range'][0] <= lat <= test['expected_lat_range'][1]
        lng_ok = test['expected_lng_range'][0] <= lng <= test['expected_lng_range'][1]
        
        if lat_ok and lng_ok:
            print(f"  ✅ PASSED")
            passed += 1
        else:
            print(f"  ❌ FAILED: Out of expected range")
            if not lat_ok:
                print(f"     Latitude {lat} not in range {test['expected_lat_range']}")
            if not lng_ok:
                print(f"     Longitude {lng} not in range {test['expected_lng_range']}")
    
    print(f"\n{'=' * 80}")
    print(f"Results: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


def test_coordinate_validation():
    """Test coordinate validation function"""
    
    print("\n" + "=" * 80)
    print("COORDINATE VALIDATION TEST")
    print("=" * 80)
    
    test_cases = [
        # Valid coordinates
        (34.0522, -118.2437, True, "Los Angeles, CA"),
        (40.7128, -74.0060, True, "New York, NY"),
        (41.8781, -87.6298, True, "Chicago, IL"),
        
        # Invalid coordinates (outside North America)
        (51.5074, -0.1278, False, "London, UK"),
        (-33.8688, 151.2093, False, "Sydney, Australia"),
        (0, 0, False, "Null Island"),
        
        # Edge cases
        (24.0, -170.0, True, "Southern boundary"),
        (71.0, -50.0, True, "Northern boundary"),
        (23.9, -170.0, False, "Just outside south"),
        (71.1, -50.0, False, "Just outside north"),
    ]
    
    passed = 0
    for lat, lng, expected, description in test_cases:
        result = validate_coordinates(lat, lng)
        status = "✅ PASSED" if result == expected else "❌ FAILED"
        print(f"{status}: {description} ({lat}, {lng}) -> {result} (expected {expected})")
        if result == expected:
            passed += 1
    
    print(f"\n{'=' * 80}")
    print(f"Results: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


def test_route_calculation():
    """Test route calculation accuracy"""
    
    print("\n" + "=" * 80)
    print("ROUTE CALCULATION TEST")
    print("=" * 80)
    
    # Test case: LA -> Denver -> Chicago
    print("\nRoute: Los Angeles, CA -> Denver, CO -> Chicago, IL")
    
    origin = GeocodingService.geocode_address("Los Angeles, CA")
    pickup = GeocodingService.geocode_address("Denver, CO")
    dropoff = GeocodingService.geocode_address("Chicago, IL")
    
    if not all([origin, pickup, dropoff]):
        print("❌ Geocoding failed")
        return False
    
    print(f"  Origin:  {origin['address'][:50]}...")
    print(f"           ({origin['lat']:.4f}, {origin['lng']:.4f})")
    print(f"  Pickup:  {pickup['address'][:50]}...")
    print(f"           ({pickup['lat']:.4f}, {pickup['lng']:.4f})")
    print(f"  Dropoff: {dropoff['address'][:50]}...")
    print(f"           ({dropoff['lat']:.4f}, {dropoff['lng']:.4f})")
    
    route = GeocodingService.calculate_route_distance(origin, pickup, dropoff)
    
    print(f"\n  Distance: {route['distance_miles']} miles")
    print(f"  Duration: {route['duration_hours']} hours")
    print(f"  Polyline points: {len(route['polyline'])}")
    print(f"  Instructions: {len(route['instructions'])} steps")
    
    # Expected: ~2000-2100 miles total (LA to Denver ~1000mi, Denver to Chicago ~1000mi)
    expected_min = 1800
    expected_max = 2300
    
    if expected_min <= route['distance_miles'] <= expected_max:
        print(f"\n  ✅ Distance within expected range ({expected_min}-{expected_max} mi)")
        return True
    else:
        print(f"\n  ❌ Distance out of range (expected {expected_min}-{expected_max} mi)")
        return False


def test_short_route():
    """Test short distance route"""
    
    print("\n" + "=" * 80)
    print("SHORT ROUTE TEST")
    print("=" * 80)
    
    # Test case: San Francisco -> San Jose (short route)
    print("\nRoute: San Francisco, CA -> Oakland, CA -> San Jose, CA")
    
    origin = GeocodingService.geocode_address("San Francisco, CA")
    pickup = GeocodingService.geocode_address("Oakland, CA")
    dropoff = GeocodingService.geocode_address("San Jose, CA")
    
    if not all([origin, pickup, dropoff]):
        print("❌ Geocoding failed")
        return False
    
    route = GeocodingService.calculate_route_distance(origin, pickup, dropoff)
    
    print(f"  Distance: {route['distance_miles']} miles")
    print(f"  Duration: {route['duration_hours']} hours")
    
    # Expected: ~50-70 miles total
    expected_min = 40
    expected_max = 80
    
    if expected_min <= route['distance_miles'] <= expected_max:
        print(f"\n  ✅ Distance within expected range ({expected_min}-{expected_max} mi)")
        return True
    else:
        print(f"\n  ❌ Distance out of range (expected {expected_min}-{expected_max} mi)")
        return False


def run_all_tests():
    """Run all geocoding tests"""
    print("\n")
    print("🚛 GEOCODING ACCURACY TEST SUITE")
    print("=" * 80)
    print("\nTesting geocoding service accuracy and reliability...")
    print("This will make requests to Nominatim and OSRM APIs.\n")
    
    results = []
    
    # Run tests with delay to respect rate limits
    import time
    
    results.append(("Geocoding Accuracy", test_geocoding_accuracy()))
    time.sleep(1)
    
    results.append(("Coordinate Validation", test_coordinate_validation()))
    
    results.append(("Long Route Calculation", test_route_calculation()))
    time.sleep(1)
    
    results.append(("Short Route Calculation", test_short_route()))
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    print(f"\n{'=' * 80}")
    print(f"Overall: {passed}/{total} test suites passed")
    
    if passed == total:
        print("\n✅ All tests passed! Geocoding service is working correctly.")
    else:
        print(f"\n❌ {total - passed} test suite(s) failed. Please review the errors above.")
    
    return passed == total


if __name__ == '__main__':
    run_all_tests()
