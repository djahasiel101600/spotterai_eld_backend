from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status
from .models import *
from datetime import timedelta


class ELDModelsTestCase(TestCase):
    def setUp(self):
        self.carrier = CarrierInfo.objects.create(
            driver_name="John Doe",
            carrier_name="Test Logistics",
            main_office_address="123 Main St",
            home_terminal_address="456 Terminal Dr",
            truck_number="TRK-001",
            shipping_docs="MANIFEST-001"
        )
        
        self.trip = Trip.objects.create(
            origin="Los Angeles, CA",
            pickup="Denver, CO",
            dropoff="Chicago, IL",
            cycle_hours_used=0,
            start_time=timezone.now(),
            carrier_info=self.carrier
        )
    
    def test_carrier_info_creation(self):
        self.assertEqual(self.carrier.driver_name, "John Doe")
        self.assertEqual(str(self.carrier), "John Doe - Test Logistics")
    
    def test_trip_creation(self):
        self.assertEqual(self.trip.origin, "Los Angeles, CA")
        self.assertEqual(str(self.trip), "Trip from Los Angeles, CA to Chicago, IL")
    
    def test_duty_event_duration_calculation(self):
        now = timezone.now()
        event = DutyEvent.objects.create(
            trip=self.trip,
            status='driving',
            start_time=now,
            end_time=now + timedelta(hours=2),
            location="En Route",
            remarks="Driving"
        )
        self.assertAlmostEqual(event.duration_hours, 2.0, places=1)


class ELDAPITestCase(APITestCase):
    def test_health_check(self):
        url = reverse('health_check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'healthy')
    
    def test_trips_list(self):
        url = reverse('trips_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_calculate_trip_missing_data(self):
        url = reverse('calculate_trip')
        response = self.client.post(url, {})
        # Should handle missing data gracefully
        self.assertIn(response.status_code, [status.HTTP_400_BAD_REQUEST, status.HTTP_500_INTERNAL_SERVER_ERROR])


class HosConfigurationTestCase(TestCase):
    def test_hos_config_creation(self):
        config = HosConfiguration.objects.create(
            profile_id="TEST_PROFILE",
            label="Test Profile",
            max_driving_hours=11,
            max_duty_window_hours=14
        )
        self.assertEqual(config.profile_id, "TEST_PROFILE")
        self.assertEqual(str(config), "Test Profile (TEST_PROFILE)")