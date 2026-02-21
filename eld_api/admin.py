from django.contrib import admin
from .models import (
    Location, CarrierInfo, Trip, RouteResult, DutyEvent, 
    DailyLog, HosConfiguration
)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('address', 'latitude', 'longitude')
    search_fields = ('address',)


@admin.register(CarrierInfo)
class CarrierInfoAdmin(admin.ModelAdmin):
    list_display = ('driver_name', 'carrier_name', 'truck_number', 'created_at')
    search_fields = ('driver_name', 'carrier_name', 'truck_number')
    list_filter = ('created_at',)


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ('origin', 'pickup', 'dropoff', 'start_time', 'created_at')
    list_filter = ('start_time', 'created_at')
    search_fields = ('origin', 'pickup', 'dropoff')
    date_hierarchy = 'start_time'


@admin.register(RouteResult)
class RouteResultAdmin(admin.ModelAdmin):
    list_display = ('trip', 'distance_miles', 'duration_hours', 'created_at')
    list_filter = ('created_at',)
    readonly_fields = ('created_at',)


@admin.register(DutyEvent)
class DutyEventAdmin(admin.ModelAdmin):
    list_display = ('trip', 'status', 'start_time', 'end_time', 'location')
    list_filter = ('status', 'start_time')
    search_fields = ('location', 'remarks')
    date_hierarchy = 'start_time'
    
    def duration_display(self, obj):
        return f"{obj.duration_hours:.2f} hours"
    duration_display.short_description = 'Duration'


@admin.register(DailyLog)
class DailyLogAdmin(admin.ModelAdmin):
    list_display = ('trip', 'date', 'driving_hours', 'total_miles')
    list_filter = ('date',)
    date_hierarchy = 'date'
    readonly_fields = ('created_at', 'updated_at')
    
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        obj.calculate_totals()


@admin.register(HosConfiguration)
class HosConfigurationAdmin(admin.ModelAdmin):
    list_display = ('profile_id', 'label', 'max_driving_hours', 'max_duty_window_hours')
    search_fields = ('profile_id', 'label')
    readonly_fields = ('created_at', 'updated_at')