from rest_framework import serializers
from .models import (
    Location, CarrierInfo, Trip, RouteResult, 
    DutyEvent, DailyLog, HosConfiguration
)


class LocationSerializer(serializers.ModelSerializer):
    lat = serializers.FloatField(source='latitude')
    lng = serializers.FloatField(source='longitude')
    
    class Meta:
        model = Location
        fields = ['address', 'lat', 'lng']


class CarrierInfoSerializer(serializers.ModelSerializer):
    driverName = serializers.CharField(source='driver_name')
    carrierName = serializers.CharField(source='carrier_name')
    mainOfficeAddress = serializers.CharField(source='main_office_address')
    homeTerminalAddress = serializers.CharField(source='home_terminal_address')
    truckNumber = serializers.CharField(source='truck_number')
    shippingDocs = serializers.CharField(source='shipping_docs')
    
    class Meta:
        model = CarrierInfo
        fields = [
            'driverName', 'carrierName', 'mainOfficeAddress', 
            'homeTerminalAddress', 'truckNumber', 'shippingDocs'
        ]
    
    def create(self, validated_data):
        return CarrierInfo.objects.create(**validated_data)


class TripInputsSerializer(serializers.ModelSerializer):
    cycleHoursUsed = serializers.FloatField(source='cycle_hours_used')
    startTime = serializers.DateTimeField(source='start_time')
    
    class Meta:
        model = Trip
        fields = ['origin', 'pickup', 'dropoff', 'cycleHoursUsed', 'startTime']

    def create(self, validated_data):
        return Trip.objects.create(**validated_data)


class RouteResultSerializer(serializers.ModelSerializer):
    distanceMiles = serializers.FloatField(source='distance_miles')
    durationHours = serializers.FloatField(source='duration_hours')
    points = serializers.SerializerMethodField()
    
    class Meta:
        model = RouteResult
        fields = ['distanceMiles', 'durationHours', 'polyline', 'instructions', 'points']
    
    def get_points(self, obj):
        return {
            'origin': LocationSerializer(obj.origin_location).data,
            'pickup': LocationSerializer(obj.pickup_location).data,
            'dropoff': LocationSerializer(obj.dropoff_location).data,
        }


class DutyEventSerializer(serializers.ModelSerializer):
    start = serializers.DateTimeField(source='start_time')
    end = serializers.DateTimeField(source='end_time')
    lat = serializers.FloatField(source='latitude', required=False, allow_null=True)
    lng = serializers.FloatField(source='longitude', required=False, allow_null=True)
    startHour = serializers.SerializerMethodField()
    endHour = serializers.SerializerMethodField()
    startTime = serializers.SerializerMethodField()
    endTime = serializers.SerializerMethodField()
    
    class Meta:
        model = DutyEvent
        fields = ['status', 'start', 'end', 'location', 'remarks', 'lat', 'lng', 'miles',
                  'startHour', 'endHour', 'startTime', 'endTime']
    
    def _get_context_date(self):
        """Get the date context from the parent DailyLog serializer if available"""
        if hasattr(self, 'context') and 'log_date' in self.context:
            return self.context['log_date']
        return None
    
    def get_startHour(self, obj):
        """Convert start_time to hour of day (0-24)"""
        if obj.start_time:
            context_date = self._get_context_date()
            # If this event started before the context date, it's a continuation - start at 0
            if context_date and obj.start_time.date() < context_date:
                return 0.0
            return round(obj.start_time.hour + obj.start_time.minute / 60.0, 2)
        return 0
    
    def get_endHour(self, obj):
        """Convert end_time to hour of day (0-24), capped at 24 if event crosses midnight"""
        if obj.end_time and obj.start_time:
            context_date = self._get_context_date()
            
            # If viewing from a specific date context
            if context_date:
                # Event ends after this date - cap at 24
                if obj.end_time.date() > context_date:
                    return 24.0
                # Event started before this date (continuation) - use actual end hour
                if obj.start_time.date() < context_date:
                    return round(obj.end_time.hour + obj.end_time.minute / 60.0, 2)
            
            # Check if event crosses midnight (end date > start date)
            if obj.end_time.date() > obj.start_time.date():
                # Event crosses midnight - cap at 24 for this day's display
                return 24.0
            
            hour = obj.end_time.hour + obj.end_time.minute / 60.0
            # Handle exact midnight (00:00) as 24 for end times on same day
            if hour == 0 and obj.start_time.hour > 0:
                return 24.0
            return round(hour, 2)
        elif obj.end_time:
            return round(obj.end_time.hour + obj.end_time.minute / 60.0, 2)
        return 24
    
    def get_startTime(self, obj):
        """Format start_time as HH:MM string"""
        if obj.start_time:
            context_date = self._get_context_date()
            # If this event started before the context date, show 00:00 (continued)
            if context_date and obj.start_time.date() < context_date:
                return '00:00'
            return obj.start_time.strftime('%H:%M')
        return '00:00'
    
    def get_endTime(self, obj):
        """Format end_time as HH:MM string"""
        if obj.end_time and obj.start_time:
            context_date = self._get_context_date()
            
            if context_date:
                # Event ends after this date - show 24:00
                if obj.end_time.date() > context_date:
                    return '24:00'
            elif obj.end_time.date() > obj.start_time.date():
                # Event crosses midnight - show 24:00 for this day
                return '24:00'
            
            return obj.end_time.strftime('%H:%M')
        elif obj.end_time:
            return obj.end_time.strftime('%H:%M')
        return '24:00'


class DailyLogSerializer(serializers.ModelSerializer):
    events = serializers.SerializerMethodField()
    totals = serializers.ReadOnlyField()
    totalMiles = serializers.FloatField(source='total_miles')
    
    class Meta:
        model = DailyLog
        fields = ['date', 'events', 'totals', 'totalMiles']
    
    def get_events(self, obj):
        """Serialize events with the log date context for proper hour calculations"""
        events = obj.events
        # Pass the log date to the event serializer so it can calculate hours correctly
        serializer = DutyEventSerializer(events, many=True, context={'log_date': obj.date})
        return serializer.data


class TripCalculationRequestSerializer(serializers.Serializer):
    """Serializer for trip calculation requests"""
    inputs = TripInputsSerializer()
    carrier = CarrierInfoSerializer(required=False)
    hosConfig = serializers.JSONField(required=False)


class TripCalculationResponseSerializer(serializers.Serializer):
    """Serializer for trip calculation responses"""
    route = RouteResultSerializer()
    events = DutyEventSerializer(many=True)
    logs = DailyLogSerializer(many=True)
    intel = serializers.CharField(allow_null=True, required=False)


class HosConfigurationSerializer(serializers.ModelSerializer):
    profileId = serializers.CharField(source='profile_id')
    shiftRules = serializers.SerializerMethodField()
    breakRules = serializers.SerializerMethodField()
    cycleRules = serializers.SerializerMethodField()
    
    class Meta:
        model = HosConfiguration
        fields = [
            'profileId', 'label', 'shiftRules', 'breakRules', 'cycleRules'
        ]
    
    def get_shiftRules(self, obj):
        return {
            'minOffDutyBeforeShiftHours': obj.min_off_duty_before_shift_hours,
            'maxDrivingHours': obj.max_driving_hours,
            'maxDutyWindowHours': obj.max_duty_window_hours,
            'allowDrivingAfterDutyWindow': obj.allow_driving_after_duty_window,
        }
    
    def get_breakRules(self, obj):
        return {
            'requiredAfterDrivingHours': obj.required_break_after_driving_hours,
            'breakDurationMinutes': obj.break_duration_minutes,
            'mustBeContinuous': obj.break_must_be_continuous,
            'qualifyingStatuses': ['off_duty', 'sleeper_berth']  # Default values
        }
    
    def get_cycleRules(self, obj):
        return {
            'defaultCycleId': obj.default_cycle,
            'restart': {
                'enabled': obj.restart_enabled,
                'durationHours': obj.restart_duration_hours,
                'mustBeContinuous': obj.restart_must_be_continuous
            }
        }


class SavedTripSerializer(serializers.ModelSerializer):
    """Complete serializer for saved trips matching frontend SavedTrip type"""
    id = serializers.CharField(read_only=True)  # Convert integer ID to string
    inputs = serializers.SerializerMethodField()
    carrier = CarrierInfoSerializer(source='carrier_info', read_only=True)
    route = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    logs = serializers.SerializerMethodField()
    hosConfig = serializers.JSONField(source='hos_config')
    intel = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    timestamp = serializers.SerializerMethodField()
    
    class Meta:
        model = Trip
        fields = [
            'id', 'timestamp', 'inputs', 'carrier', 'route', 
            'events', 'logs', 'hosConfig', 'intel'
        ]
    
    def get_timestamp(self, obj):
        """Return timestamp as ISO string"""
        return obj.created_at.isoformat()
    
    def get_inputs(self, obj):
        return {
            'origin': obj.origin,
            'pickup': obj.pickup,
            'dropoff': obj.dropoff,
            'cycleHoursUsed': obj.cycle_hours_used,
            'startTime': obj.start_time.isoformat() if obj.start_time else None,
        }
    
    def get_route(self, obj):
        """Get route data if exists"""
        try:
            route = obj.route
            return RouteResultSerializer(route).data
        except RouteResult.DoesNotExist:
            return None
        except AttributeError:
            return None
    
    def get_events(self, obj):
        """Get all duty events for this trip"""
        events = obj.duty_events.all().order_by('start_time')
        return DutyEventSerializer(events, many=True).data
    
    def get_logs(self, obj):
        """Get daily logs for this trip. Use each DailyLog's .events so continuations
        (e.g. sleeper berth from previous night 00:00-09:15) are included and serialized
        with log_date for correct startHour/endHour."""
        logs = obj.daily_logs.all().order_by('date')
        serialized_logs = []
        for log in logs:
            # Use log.events (includes events that start on this date OR continue from previous day)
            log_events = log.events
            serializer = DutyEventSerializer(
                log_events, many=True, context={'log_date': log.date}
            )
            serialized_logs.append({
                'date': log.date.isoformat(),
                'events': serializer.data,
                'totals': log.totals,
                'totalMiles': log.total_miles,
            })
        return serialized_logs