from django.db import models
from django.contrib.auth.models import User
import json


class DutyStatusChoices(models.TextChoices):
    OFF_DUTY = 'off_duty', 'Off Duty'
    SLEEPER_BERTH = 'sleeper_berth', 'Sleeper Berth'
    DRIVING = 'driving', 'Driving'
    ON_DUTY_NOT_DRIVING = 'on_duty_not_driving', 'On Duty (Not Driving)'


class CycleChoices(models.TextChoices):
    SIXTY_IN_SEVEN = '60_in_7', '60 hours in 7 days'
    SEVENTY_IN_EIGHT = '70_in_8', '70 hours in 8 days'


class Location(models.Model):
    address = models.CharField(max_length=500)
    latitude = models.FloatField()
    longitude = models.FloatField()
    
    def __str__(self):
        return self.address
    
    class Meta:
        db_table = 'eld_location'


class CarrierInfo(models.Model):
    driver_name = models.CharField(max_length=200)
    carrier_name = models.CharField(max_length=200)
    main_office_address = models.CharField(max_length=500)
    home_terminal_address = models.CharField(max_length=500)
    truck_number = models.CharField(max_length=100)
    shipping_docs = models.CharField(max_length=200)
    co_driver_name = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.driver_name} - {self.carrier_name}"
    
    class Meta:
        db_table = 'eld_carrier_info'


class Trip(models.Model):
    origin = models.CharField(max_length=500)
    pickup = models.CharField(max_length=500)
    dropoff = models.CharField(max_length=500)
    cycle_hours_used = models.FloatField(default=0)
    start_time = models.DateTimeField()
    carrier_info = models.ForeignKey(CarrierInfo, on_delete=models.CASCADE, null=True, blank=True)
    hos_config = models.JSONField(default=dict, blank=True)  # HOS configuration for this trip
    intel = models.TextField(blank=True, null=True)  # AI-generated trip intelligence
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Trip from {self.origin} to {self.dropoff}"
    
    class Meta:
        db_table = 'eld_trip'


class RouteResult(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='route')
    distance_miles = models.FloatField()
    duration_hours = models.FloatField()
    polyline = models.JSONField()  # Store as JSON array of [lat, lng] pairs
    instructions = models.JSONField()  # Store as JSON array of strings
    
    # Location points
    origin_location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='routes_as_origin')
    pickup_location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='routes_as_pickup')
    dropoff_location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='routes_as_dropoff')
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Route for {self.trip}"
    
    class Meta:
        db_table = 'eld_route_result'


class DutyEvent(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='duty_events', db_index=True)
    status = models.CharField(max_length=50, choices=DutyStatusChoices.choices, db_index=True)
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    location = models.CharField(max_length=500)
    remarks = models.TextField()
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    miles = models.FloatField(default=0, help_text="Miles covered during this event")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.get_status_display()} - {self.start_time.strftime('%Y-%m-%d %H:%M')}"
    
    @property
    def duration_hours(self):
        """Calculate duration in hours"""
        if self.end_time and self.start_time:
            delta = self.end_time - self.start_time
            return delta.total_seconds() / 3600
        return 0
    
    class Meta:
        db_table = 'eld_duty_event'
        ordering = ['start_time']


class DailyLog(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='daily_logs', db_index=True)
    date = models.DateField(db_index=True)
    total_miles = models.FloatField(default=0)
    
    # Calculated totals for each duty status (in hours)
    off_duty_hours = models.FloatField(default=0)
    sleeper_berth_hours = models.FloatField(default=0)
    driving_hours = models.FloatField(default=0)
    on_duty_not_driving_hours = models.FloatField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Daily Log - {self.date}"
    
    @property
    def events(self):
        """Get all duty events for this day, including events that cross into this day"""
        from django.db.models import Q
        from datetime import datetime, time
        
        # Get events that:
        # 1. Start on this date, OR
        # 2. Started before this date but end on or after this date (continuation)
        start_of_day = datetime.combine(self.date, time(0, 0, 0))
        
        return self.trip.duty_events.filter(
            Q(start_time__date=self.date) |  # Events starting today
            Q(start_time__date__lt=self.date, end_time__date__gte=self.date)  # Events continuing from yesterday
        ).order_by('start_time')
    
    @property
    def totals(self):
        """Return totals as a dictionary matching the frontend format"""
        return {
            'off_duty': self.off_duty_hours,
            'sleeper_berth': self.sleeper_berth_hours,
            'driving': self.driving_hours,
            'on_duty_not_driving': self.on_duty_not_driving_hours,
        }
    
    def calculate_totals(self):
        """Calculate and update totals based on duty events"""
        events = self.events
        totals = {
            'off_duty': 0,
            'sleeper_berth': 0,
            'driving': 0,
            'on_duty_not_driving': 0,
        }
        
        for event in events:
            duration = event.duration_hours
            if event.status in totals:
                totals[event.status] += duration
        
        self.off_duty_hours = totals['off_duty']
        self.sleeper_berth_hours = totals['sleeper_berth']  
        self.driving_hours = totals['driving']
        self.on_duty_not_driving_hours = totals['on_duty_not_driving']
        self.save()
    
    class Meta:
        db_table = 'eld_daily_log'
        unique_together = ['trip', 'date']
        ordering = ['date']


class HosConfiguration(models.Model):
    """Model for Hours of Service configuration"""
    profile_id = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=200)
    
    # Shift rules
    min_off_duty_before_shift_hours = models.FloatField(default=10)
    max_driving_hours = models.FloatField(default=11)
    max_duty_window_hours = models.FloatField(default=14)
    allow_driving_after_duty_window = models.BooleanField(default=False)
    
    # Break rules
    required_break_after_driving_hours = models.FloatField(default=8)
    break_duration_minutes = models.IntegerField(default=30)
    break_must_be_continuous = models.BooleanField(default=True)
    
    # Cycle rules
    default_cycle = models.CharField(max_length=20, choices=CycleChoices.choices, default='70_in_8')
    restart_enabled = models.BooleanField(default=True)
    restart_duration_hours = models.IntegerField(default=34)
    restart_must_be_continuous = models.BooleanField(default=True)
    
    # Additional configuration stored as JSON
    extended_config = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.label} ({self.profile_id})"
    
    class Meta:
        db_table = 'eld_hos_configuration'